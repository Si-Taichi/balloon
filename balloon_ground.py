import serial
import time
import threading
import base64
from datetime import datetime
from collections import deque
import traceback

from dash import Dash, html, dcc, Input, Output, State, callback_context
import dash_bootstrap_components as dbc

# Global variables for both serial connections
image_data = {}
frame_count = 0
packet = b""
pack_size = 0
apogee = False
ser1 = None
ser2 = None
running1 = False
running2 = False
frame_count_local = 1

packets_received_port1 = 0
packets_received_port2 = 0
telemetry_log = deque(maxlen=500)
rssi_history_port1 = deque(maxlen=100)
rssi_history_port2 = deque(maxlen=100)
time_history = deque(maxlen=100)
current_image = None
connection_status_port1 = "Disconnected"
connection_status_port2 = "Disconnected"

# GPS data
current_lat = None
current_lon = None
current_alt = None
gps_history = deque(maxlen=100)

# Data storage for both ports with timestamps
data_buffer = {
    'FC': {'port1': None, 'port2': None, 'time1': None, 'time2': None},
    'RS': {'port1': None, 'port2': None, 'time1': None, 'time2': None},
    'PS': {'port1': None, 'port2': None, 'time1': None, 'time2': None},
    'IX': {'port1': None, 'port2': None, 'time1': None, 'time2': None},
    'AP': {'port1': None, 'port2': None, 'time1': None, 'time2': None},
    'PL': {'port1': None, 'port2': None, 'time1': None, 'time2': None},
    'GS': {'port1': None, 'port2': None, 'time1': None, 'time2': None},
}

def log(message):
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    telemetry_log.append(f"[{timestamp}] {message}")
    with open("log.txt", "a", encoding="utf-8") as t:
        t.write(f"[{timestamp}] {message}\n")

def log_image_bytes(header, data, port_num, packet_num=None):
    """Log image-related bytes to a separate file"""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    with open("image_bytes_log.txt", "a", encoding="utf-8") as img_log:
        if packet_num is not None:
            img_log.write(f"[{timestamp}] Port{port_num} {header}: Packet #{packet_num}, {len(data)} bytes\n")
        else:
            img_log.write(f"[{timestamp}] Port{port_num} {header}: {len(data)} bytes\n")

def get_best_data(header, port_name):
    """Get data from buffer, preferring both ports available, then falling back to single port"""
    buffer = data_buffer.get(header)
    if not buffer:
        return None
    
    port1_data = buffer['port1']
    port2_data = buffer['port2']
    time1 = buffer['time1']
    time2 = buffer['time2']
    
    if port1_data is not None and port2_data is not None:
        if time1 >= time2:
            log("Using data from Port1 (both available, Port1 newer)")
            return port1_data
        else:
            log("Using data from Port2 (both available, Port2 newer)")
            return port2_data
    elif port1_data is not None:
        log("Using data from Port1 (Port2 unavailable)")
        return port1_data
    elif port2_data is not None:
        log("Using data from Port2 (Port1 unavailable)")
        return port2_data
    
    return None

def update_data_buffer(header, data, port_num):
    if header not in data_buffer:
        data_buffer[header] = {'port1': None, 'port2': None, 'time1': None, 'time2': None}
    
    port_key = f'port{port_num}'
    time_key = f'time{port_num}'
    data_buffer[header][port_key] = data
    data_buffer[header][time_key] = datetime.now()

def serial_worker(port, port_num):
    global ser1, ser2, running1, running2, image_data, frame_count_local, packet, pack_size, apogee
    global packets_received_port1, packets_received_port2, current_image, connection_status_port1, connection_status_port2, frame_count
    global current_lat, current_lon, current_alt, gps_history
    
    running = running1 if port_num == 1 else running2
    ser = None
    local_pack_size = 0
    local_packet = b""
    
    while running:
        try:
            ser = serial.Serial(port=port, baudrate=115200, timeout=1)
            ser.reset_input_buffer()
            if port_num == 1:
                connection_status_port1 = "Connected"
                ser1 = ser
            else:
                connection_status_port2 = "Connected"
                ser2 = ser
            log(f"✓ Port{port_num} Connected to {port}")
            break
        except Exception as e:
            log(f"✗ Port{port_num} Failed to open {port}: {e}")
            time.sleep(2)
            running = running1 if port_num == 1 else running2
            if not running:
                return
            
    try:
        while running:
            running = running1 if port_num == 1 else running2
            if not running:
                break
                
            if ser.in_waiting > 0:
                if local_pack_size > 0:
                    line = ser.read(local_pack_size + 5)
                else:
                    line = ser.readline()
                
                try:
                    header = line[:2].decode("ascii")
                except:
                    header = "XX"
                
                if local_pack_size > 0:
                    line = line[2 + 1:-2]
                    data = line
                    log(f"Port{port_num} {header}: Binary packet ({len(data)} bytes)")
                    log_image_bytes(header, data, port_num)
                    local_pack_size = 0
                else:
                    line = line[2 + 1:-1]
                    try:
                        data = line.decode("ascii")
                        log(f"Port{port_num} {header}: {data}")
                    except:
                        header = "XX"
                        data = line

                update_data_buffer(header, data, port_num)

                if header == "FC":
                    new_frame = int(data)
                    if frame_count != new_frame:
                        frame_count = new_frame
                        save_and_display_image()
                elif header == "PS":
                    local_pack_size = int(data)
                    pack_size = local_pack_size
                elif header == "IX":
                    local_packet = data
                    packet = data
                    log_image_bytes("IX", data, port_num)
                elif header == "AP":
                    local_packet = data
                    packet = data
                    apogee = True
                    save_and_display_image()
                    log(f"⚠ APOGEE DETECTED on Port{port_num}!")
                    log_image_bytes("AP", data, port_num)
                elif header == "PL":
                    packet_num = int(data)
                    best_packet = get_best_data('IX', f'port{port_num}')
                    if best_packet is None:
                        best_packet = local_packet
                    image_data[packet_num] = best_packet
                    log_image_bytes("PL", best_packet, port_num, packet_num)
                elif header == "RS":
                    try:
                        rssi_value = float(data)
                        if port_num == 1:
                            rssi_history_port1.append(rssi_value)
                        else:
                            rssi_history_port2.append(rssi_value)
                        time_history.append(datetime.now())
                    except Exception as e:
                        log(f"Port{port_num} RSSI Error: {e}")
                elif header == "GS":
                    try:
                        parts = data.split(',')
                        if len(parts) == 3:
                            lat, lon, alt = parts
                            current_lat = lat
                            current_lon = lon
                            current_alt = alt
                            gps_history.append({
                                'lat': current_lat,
                                'lon': current_lon,
                                'alt': current_alt,
                                'time': datetime.now()
                            })
                            log(f"📍 GPS: Lat={current_lat}, Lon={current_lon}, Alt={current_alt}m")
                    except Exception as e:
                        log(f"Port{port_num} GPS Error: {e}")
                else:
                    print(f"Port{port_num} raw: {line}")

                if port_num == 1:
                    packets_received_port1 += 1
                else:
                    packets_received_port2 += 1

    except Exception as e:
        log(f"Port{port_num} Error: {e}")
        traceback.print_exc()
    finally:
        if ser and ser.is_open:
            ser.close()
        if port_num == 1:
            running1 = False
            connection_status_port1 = "Disconnected"
        else:
            running2 = False
            connection_status_port2 = "Disconnected"

def save_and_display_image():
    global image_data, frame_count_local, current_image
    if not image_data:
        return
    
    try:
        byte_data = b"".join(image_data.values())

        filename = f"frame_{frame_count_local}.webp"
        with open(filename, "wb") as f:
            f.write(byte_data)
        
        current_image = base64.b64encode(byte_data).decode()
        
        log(f"✓ Saved: {filename} ({len(byte_data)/1024:.1f} KB)")
        log_image_bytes("SAVE", byte_data, 0)
        
        frame_count_local += 1
        image_data = {}  

    except Exception as e:
        log(f"Error processing image: {e}")

app = Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])

app.layout = dbc.Container([
    dcc.Interval(id='interval-component', interval=1000, n_intervals=0),

    dbc.Row([
        dbc.Col([
            html.H1("🎈 Dual Port Balloon Ground Station", className="text-center mb-4")
        ])
    ]),

    dbc.Card([
        dbc.CardHeader(html.H5("🔌 Port 1 Connection")),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    dbc.InputGroup([
                        dbc.InputGroupText("COM Port 1"),
                        dbc.Input(id="port1-input", value="COM11", type="text"),
                    ], className="mb-2"),
                ], width=3),
                dbc.Col([
                    dbc.Button("Connect Port 1", id="connect1-btn", color="success", className="me-2"),
                    dbc.Button("Disconnect Port 1", id="disconnect1-btn", color="danger"),
                ], width=3),
                dbc.Col([
                    html.Div([
                        html.H6(id="status1-indicator", className="mb-0"),
                    ])
                ], width=3),
                dbc.Col([
                    html.Div([
                        html.Small(id="stats1-display", className="text-muted")
                    ])
                ], width=3),
            ], align="center")
        ])
    ], className="mb-3"),

    dbc.Card([
        dbc.CardHeader(html.H5("🔌 Port 2 Connection")),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    dbc.InputGroup([
                        dbc.InputGroupText("COM Port 2"),
                        dbc.Input(id="port2-input", value="COM12", type="text"),
                    ], className="mb-2"),
                ], width=3),
                dbc.Col([
                    dbc.Button("Connect Port 2", id="connect2-btn", color="success", className="me-2"),
                    dbc.Button("Disconnect Port 2", id="disconnect2-btn", color="danger"),
                ], width=3),
                dbc.Col([
                    html.Div([
                        html.H6(id="status2-indicator", className="mb-0"),
                    ])
                ], width=3),
                dbc.Col([
                    html.Div([
                        html.Small(id="stats2-display", className="text-muted")
                    ])
                ], width=3),
            ], align="center")
        ])
    ], className="mb-4"),
    
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader(html.H4("📷 Latest Image")),
                dbc.CardBody([
                    html.Div(id="image-display", style={"textAlign": "center", "minHeight": "400px"}),
                    html.Hr(),
                    html.Div(id="image-info", className="text-center text-muted")
                ])
            ], className="mb-3"),

            dbc.Card([
                dbc.CardHeader(html.H4("🗺️ GPS Location")),
                dbc.CardBody([
                    html.Iframe(
                        id="map-frame",
                        style={
                            "width": "100%",
                            "height": "400px",
                            "border": "none",
                            "borderRadius": "5px"
                        }
                    ),
                    html.Hr(),
                    html.Div(id="gps-info", className="text-center text-muted")
                ])
            ])
        ], width=8),

        dbc.Col([
            dbc.Card([
                dbc.CardHeader(html.H5("📡 Port 1 Signal (RSSI)")),
                dbc.CardBody([
                    html.Div(id="rssi1-display", style={
                        "textAlign": "center",
                        "padding": "20px"
                    })
                ])
            ], className="mb-3"),

            dbc.Card([
                dbc.CardHeader(html.H5("📡 Port 2 Signal (RSSI)")),
                dbc.CardBody([
                    html.Div(id="rssi2-display", style={
                        "textAlign": "center",
                        "padding": "20px"
                    })
                ])
            ], className="mb-3"),

            dbc.Card([
                dbc.CardHeader(html.H5("📋 Telemetry Log")),
                dbc.CardBody([
                    html.Div(id="telemetry-log", **{"data-dummy": ""}, style={
                        "maxHeight": "300px",
                        "overflowY": "scroll",
                        "fontFamily": "monospace",
                        "fontSize": "11px",
                        "backgroundColor": "#1a1a1a",
                        "padding": "10px",
                        "borderRadius": "5px"
                    })
                ])
            ])
        ], width=4)
    ])
], fluid=True, className="p-4")

@app.callback(
    Output("connect1-btn", "disabled"),
    Output("disconnect1-btn", "disabled"),
    Input("connect1-btn", "n_clicks"),
    Input("disconnect1-btn", "n_clicks"),
    State("port1-input", "value"),
    prevent_initial_call=True
)
def handle_connection_port1(connect_clicks, disconnect_clicks, port):
    global running1, serial_thread1
    
    ctx = callback_context
    if not ctx.triggered:
        return False, True
    
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    if button_id == "connect1-btn" and not running1:
        running1 = True
        serial_thread1 = threading.Thread(target=serial_worker, args=(port, 1), daemon=True)
        serial_thread1.start()
        log("Port 1 Connecting...")
        return True, False
    elif button_id == "disconnect1-btn" and running1:
        running1 = False
        if ser1 and ser1.is_open:
            ser1.close()
        log("Port 1 Connection closed by user")
        return False, True
    
    return running1, not running1

@app.callback(
    Output("connect2-btn", "disabled"),
    Output("disconnect2-btn", "disabled"),
    Input("connect2-btn", "n_clicks"),
    Input("disconnect2-btn", "n_clicks"),
    State("port2-input", "value"),
    prevent_initial_call=True
)
def handle_connection_port2(connect_clicks, disconnect_clicks, port):
    global running2, serial_thread2
    
    ctx = callback_context
    if not ctx.triggered:
        return False, True
    
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    if button_id == "connect2-btn" and not running2:
        running2 = True
        serial_thread2 = threading.Thread(target=serial_worker, args=(port, 2), daemon=True)
        serial_thread2.start()
        log("Port 2 Connecting...")
        return True, False
    elif button_id == "disconnect2-btn" and running2:
        running2 = False
        if ser2 and ser2.is_open:
            ser2.close()
        log("Port 2 Connection closed by user")
        return False, True
    
    return running2, not running2

@app.callback(
    Output("status1-indicator", "children"),
    Output("status1-indicator", "className"),
    Output("stats1-display", "children"),
    Output("status2-indicator", "children"),
    Output("status2-indicator", "className"),
    Output("stats2-display", "children"),
    Output("image-display", "children"),
    Output("image-info", "children"),
    Output("telemetry-log", "children"),
    Output("rssi1-display", "children"),
    Output("rssi2-display", "children"),
    Output("map-frame", "srcDoc"),
    Output("gps-info", "children"),
    Input("interval-component", "n_intervals")
)
def update_dashboard(n):
    global connection_status_port1, connection_status_port2, packets_received_port1, packets_received_port2
    global frame_count_local, current_image, current_lat, current_lon, current_alt

    if connection_status_port1 == "Connected":
        status1 = html.Span("● Connected", style={"color": "#00ff00"})
        status1_class = "mb-0"
    else:
        status1 = html.Span("● Disconnected", style={"color": "#ff4444"})
        status1_class = "mb-0"

    stats1 = f"Packets: {packets_received_port1}"

    if connection_status_port2 == "Connected":
        status2 = html.Span("● Connected", style={"color": "#00ff00"})
        status2_class = "mb-0"
    else:
        status2 = html.Span("● Disconnected", style={"color": "#ff4444"})
        status2_class = "mb-0"

    stats2 = f"Packets: {packets_received_port2}"

    if current_image:
        image_display = html.Img(
            src=f"data:image/webp;base64,{current_image}",
            style={"maxWidth": "100%", "maxHeight": "500px", "borderRadius": "5px"}
        )
        image_info = f"Frame #{frame_count_local - 1} | {len(base64.b64decode(current_image))/1024:.1f} KB"
    else:
        image_display = html.Div(
            "No image received yet",
            style={"padding": "100px", "color": "#666"}
        )
        image_info = "Waiting for data..."

    log_entries = [html.Div(entry, style={"color": "#00ff00"}) for entry in list(telemetry_log)]

    if len(rssi_history_port1) > 0:
        current_rssi1 = rssi_history_port1[-1]
        if current_rssi1 > -70:
            rssi_color1 = "#00ff00"
        elif current_rssi1 > -85:
            rssi_color1 = "#ffaa00"
        else:
            rssi_color1 = "#ff4444"
        
        rssi1_display = html.Div([
            html.Div(f"{current_rssi1}", style={
                "fontSize": "36px", 
                "fontWeight": "bold",
                "color": rssi_color1
            }),
            html.Div("dBm", style={
                "fontSize": "16px", 
                "color": "#aaa", 
                "marginTop": "5px"
            })
        ])
    else:
        rssi1_display = html.Div([
            html.Div("--", style={
                "fontSize": "36px", 
                "fontWeight": "bold",
                "color": "#666"
            }),
            html.Div("No signal", style={
                "fontSize": "12px", 
                "color": "#666", 
                "marginTop": "5px"
            })
        ])

    if len(rssi_history_port2) > 0:
        current_rssi2 = rssi_history_port2[-1]
        if current_rssi2 > -70:
            rssi_color2 = "#00ff00"
        elif current_rssi2 > -85:
            rssi_color2 = "#ffaa00"
        else:
            rssi_color2 = "#ff4444"
        
        rssi2_display = html.Div([
            html.Div(f"{current_rssi2}", style={
                "fontSize": "36px", 
                "fontWeight": "bold",
                "color": rssi_color2
            }),
            html.Div("dBm", style={
                "fontSize": "16px", 
                "color": "#aaa", 
                "marginTop": "5px"
            })
        ])
    else:
        rssi2_display = html.Div([
            html.Div("--", style={
                "fontSize": "36px", 
                "fontWeight": "bold",
                "color": "#666"
            }),
            html.Div("No signal", style={
                "fontSize": "12px", 
                "color": "#666", 
                "marginTop": "5px"
            })
        ])

    # GPS Map and Info
    if current_lat is not None and current_lon is not None:
        # Create path from GPS history
        path_points = []
        if len(gps_history) > 1:
            for point in gps_history:
                path_points.append(f"{{lat: {point['lat']}, lng: {point['lon']}}}")
        
        path_coordinates = ",".join(path_points) if path_points else ""
        
        map_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ margin: 0; padding: 0; }}
                #map {{ height: 100vh; width: 100%; }}
            </style>
        </head>
        <body>
            <div id="map"></div>
            <script>
                function initMap() {{
                    const position = {{ lat: {current_lat}, lng: {current_lon} }};
                    const map = new google.maps.Map(document.getElementById("map"), {{
                        zoom: 15,
                        center: position,
                        mapTypeId: 'hybrid'
                    }});
                    
                    // Current position marker
                    const marker = new google.maps.Marker({{
                        position: position,
                        map: map,
                        title: "Balloon Position",
                        icon: {{
                            path: google.maps.SymbolPath.CIRCLE,
                            scale: 8,
                            fillColor: "#FF0000",
                            fillOpacity: 0.8,
                            strokeColor: "#FFFFFF",
                            strokeWeight: 2
                        }}
                    }});
                    
                    const infoWindow = new google.maps.InfoWindow({{
                        content: `<div style="color: black;"><b>Current Position</b><br>Lat: {current_lat}<br>Lon: {current_lon}<br>Alt: {current_alt}m</div>`
                    }});
                    
                    marker.addListener("click", () => {{
                        infoWindow.open(map, marker);
                    }});
                    
                    // Draw path if available
                    {f'''
                    const pathCoordinates = [{path_coordinates}];
                    const flightPath = new google.maps.Polyline({{
                        path: pathCoordinates,
                        geodesic: true,
                        strokeColor: "#00FF00",
                        strokeOpacity: 0.8,
                        strokeWeight: 3
                    }});
                    flightPath.setMap(map);
                    ''' if path_coordinates else ''}
                }}
            </script>
            <script src="https://maps.googleapis.com/maps/api/js?key=YOUR_API_KEY&callback=initMap" async defer></script>
        </body>
        </html>
        """
        
        gps_info = f"📍 Lat: {current_lat:.6f} | Lon: {current_lon:.6f} | Alt: {current_alt:.1f}m"
    else:
        map_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {
                    margin: 0;
                    padding: 0;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    background-color: #2c2c2c;
                    color: #666;
                    font-family: Arial, sans-serif;
                }
            </style>
        </head>
        <body>
            <div>No GPS data received yet</div>
        </body>
        </html>
        """
        gps_info = "Waiting for GPS data..."
    
    return (status1, status1_class, stats1, 
            status2, status2_class, stats2,
            image_display, image_info, log_entries, 
            rssi1_display, rssi2_display,
            map_html, gps_info)

if __name__ == "__main__":
    app.clientside_callback(
        """
        function(children) {
            const logDiv = document.getElementById("telemetry-log");
            if (logDiv) {
                logDiv.scrollTop = logDiv.scrollHeight;
            }
            return "";
        }
        """,
        Output("telemetry-log", "data-dummy"),
        Input("telemetry-log", "children")
    )

    app.run(debug=True, host='0.0.0.0', port=8050, use_reloader=False)
