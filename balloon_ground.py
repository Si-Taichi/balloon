import serial
import time
import threading
import os
import base64
from datetime import datetime
from collections import deque
import traceback

from dash import Dash, html, dcc, Input, Output, State, callback_context
import dash_bootstrap_components as dbc
import plotly.graph_objs as go

image_data = {}
frame_count = 0
packet = b""
pack_size = 0
apogee = False
ser = None
running = False

packets_received = 0
telemetry_log = deque(maxlen=500)
rssi_history = deque(maxlen=100)
time_history = deque(maxlen=100)
current_image = None
connection_status = "Disconnected"

def log(message):
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    telemetry_log.append(f"[{timestamp}] {message}")
    with open("log.txt", "a") as f:
        f.write(f"[{timestamp}] {message}\n")

def serial_worker(port):
    global ser, running, image_data, frame_count, packet, pack_size, apogee
    global packets_received, current_image, connection_status
    
    while running:
        try:
            ser = serial.Serial(port=port, baudrate=115200, timeout=1)
            ser.reset_input_buffer()
            connection_status = "Connected"
            log(f"✓ Connected to {port}")
            break
        except Exception as e:
            log(f"✗ Failed to open {port}: {e}")
            time.sleep(2)
            if not running:
                return
    
    try:
        while running:
            if ser.in_waiting > 0:
                if pack_size > 0:
                    line = ser.read(pack_size + 5)
                else:
                    line = ser.readline()
                
                try:
                    header = line[:2].decode("ascii")
                except:
                    header = "XX"
                
                if pack_size > 0:
                    line = line[2 + 1:-2]
                    data = line
                    log(f"{header}: Binary packet ({len(data)} bytes)")
                    pack_size = 0
                else:
                    line = line[2 + 1:-1]
                    try:
                        data = line.decode("ascii")
                        log(f"{header}: {data}")
                    except:
                        header = "XX"
                        data = line
                
                if header == "FC":
                    new_frame = int(data)
                    if frame_count != new_frame:
                        frame_count = new_frame
                        save_and_display_image()
                elif header == "PS":
                    pack_size = int(data)
                elif header == "IX":
                    packet = data
                elif header == "AP":
                    packet = data
                    apogee = True
                    save_and_display_image()
                    log("⚠ APOGEE DETECTED!")
                elif header == "PL":
                    image_data[int(data)] = packet
                elif header == "RS":
                    try:
                        rssi_value = int(data)
                        rssi_history.append(rssi_value)
                        time_history.append(datetime.now())
                    except:
                        pass
                
                packets_received += 1
                
    except Exception as e:
        log(f"Error: {e}")
        traceback.print_exc()
    finally:
        if ser and ser.is_open:
            ser.close()
        running = False
        connection_status = "Disconnected"

def save_and_display_image():
    global image_data, frame_count, current_image
    
    if not image_data:
        return
    
    try:
        byte_data = b"".join(image_data.values())
        
        filename = f"frame_{frame_count}.webp"
        path = os.path.join("img/", filename)
        with open(path, "wb") as f:
            f.write(byte_data)
        
        current_image = base64.b64encode(byte_data).decode()
        
        log(f"✓ Saved: {filename} ({len(byte_data)/1024:.1f} KB)")
        

        image_data = {}
        
    except Exception as e:
        log(f"Error processing image: {e}")

app = Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])

app.layout = dbc.Container([
    dcc.Interval(id='interval-component', interval=1000, n_intervals=0),
    
    dbc.Row([
        dbc.Col([
            html.H1("🎈 Balloon Ground Station", className="text-center mb-4")
        ])
    ]),
    
    dbc.Card([
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    dbc.InputGroup([
                        dbc.InputGroupText("COM Port"),
                        dbc.Input(id="port-input", value="COM4", type="text"),
                    ], className="mb-2"),
                ], width=3),
                dbc.Col([
                    dbc.Button("Connect", id="connect-btn", color="success", className="me-2"),
                    dbc.Button("Disconnect", id="disconnect-btn", color="danger"),
                ], width=3),
                dbc.Col([
                    html.Div([
                        html.H5(id="status-indicator", className="mb-0"),
                    ])
                ], width=3),
                dbc.Col([
                    html.Div([
                        html.Small(id="stats-display", className="text-muted")
                    ])
                ], width=3),
            ], align="center")
        ])
    ], className="mb-4"),
    
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader(html.H4("📷 Live Image Feed")),
                dbc.CardBody([
                    html.Div(id="image-display", style={"textAlign": "center", "minHeight": "400px"}),
                    html.Hr(),
                    html.Div(id="image-info", className="text-center text-muted")
                ])
            ])
        ], width=8),
        
        dbc.Col([
            dbc.Card([
                dbc.CardHeader(html.H5("📡 Signal Strength (RSSI)")),
                dbc.CardBody([
                    dcc.Graph(id="rssi-graph", config={'displayModeBar': False})
                ])
            ], className="mb-3"),
            
            dbc.Card([
                dbc.CardHeader(html.H5("📋 Telemetry Log")),
                dbc.CardBody([
                    html.Div(id="telemetry-log", style={
                        "maxHeight": "300px",
                        "overflowY": "scroll",
                        "fontFamily": "monospace",
                        "fontSize": "12px",
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
    Output("connect-btn", "disabled"),
    Output("disconnect-btn", "disabled"),
    Input("connect-btn", "n_clicks"),
    Input("disconnect-btn", "n_clicks"),
    State("port-input", "value"),
    prevent_initial_call=True
)

def handle_connection(connect_clicks, disconnect_clicks, port):
    global running, serial_thread
    
    ctx = callback_context
    if not ctx.triggered:
        return False, True
    
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    if button_id == "connect-btn" and not running:
        running = True
        serial_thread = threading.Thread(target=serial_worker, args=(port,), daemon=True)
        serial_thread.start()
        return True, False
    elif button_id == "disconnect-btn" and running:
        running = False
        if ser and ser.is_open:
            ser.close()
        log("Connection closed by user")
        return False, True
    
    return running, not running

@app.callback(
    Output("status-indicator", "children"),
    Output("status-indicator", "className"),
    Output("stats-display", "children"),
    Output("image-display", "children"),
    Output("image-info", "children"),
    Output("telemetry-log", "children"),
    Output("rssi-graph", "figure"),
    Input("interval-component", "n_intervals")
)
def update_dashboard(n):
    global connection_status, packets_received, frame_count, current_image
    
    if connection_status == "Connected":
        status = html.Span("● Connected", style={"color": "#00ff00"})
        status_class = "mb-0"
    else:
        status = html.Span("● Disconnected", style={"color": "#ff4444"})
        status_class = "mb-0"
    
    stats = f"Packets: {packets_received} | Frame: {frame_count}"
    

    if current_image:
        image_display = html.Img(
            src=f"data:image/webp;base64,{current_image}",
            style={"maxWidth": "100%", "maxHeight": "500px", "borderRadius": "5px"}
        )
        image_info = f"Frame #{frame_count} | {len(base64.b64decode(current_image))/1024:.1f} KB"
    else:
        image_display = html.Div(
            "No image received yet",
            style={"padding": "100px", "color": "#666"}
        )
        image_info = "Waiting for data..."

    log_entries = [html.Div(entry, style={"color": "#00ff00"}) for entry in list(telemetry_log)]
    
    if len(rssi_history) > 0:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=list(range(len(rssi_history))),
            y=list(rssi_history),
            mode='lines',
            line=dict(color='#00ff00', width=2),
            fill='tozeroy',
            fillcolor='rgba(0, 255, 0, 0.1)'
        ))
        fig.update_layout(
            margin=dict(l=20, r=20, t=20, b=20),
            paper_bgcolor='#1a1a1a',
            plot_bgcolor='#1a1a1a',
            font=dict(color='#ffffff'),
            xaxis=dict(showgrid=False, title="Time"),
            yaxis=dict(showgrid=True, gridcolor='#333', title="RSSI (dBm)"),
            height=200
        )
    else:
        fig = go.Figure()
        fig.update_layout(
            margin=dict(l=20, r=20, t=20, b=20),
            paper_bgcolor='#1a1a1a',
            plot_bgcolor='#1a1a1a',
            font=dict(color='#666'),
            xaxis=dict(showgrid=False),
            yaxis=dict(showgrid=False),
            height=200,
            annotations=[{
                'text': 'No RSSI data',
                'xref': 'paper',
                'yref': 'paper',
                'showarrow': False,
                'font': {'size': 14, 'color': '#666'}
            }]
        )
    
    return status, status_class, stats, image_display, image_info, log_entries, fig

if __name__ == "__main__":
    print("Starting...")
    print("📡 Open http://localhost:8050")
    app.run(debug=True, host='0.0.0.0', port=8050, use_reloader=False)