from __future__ import annotations

from textwrap import dedent

def _yaml_escape(value: str) -> str:
    return value.replace('"', '\\"')


def render_client_yaml(
    addr: str,
    psk: str,
    *,
    interface: str = "",
    local_ip: str = "",
    router_mac: str = "",
) -> str:
    addr = _yaml_escape(addr)
    psk = _yaml_escape(psk)
    interface = _yaml_escape(interface.strip())
    local_ip = _yaml_escape(local_ip.strip())
    router_mac = _yaml_escape(router_mac.strip())

    lines = [
        'mode: "client"',
        f'psk: "{psk}"',
        'profile: "latency"',
        "verbose: true",
        "heartbeat: 2",
        "",
        "paths:",
        '  - transport: "quantummux"',
        f'    addr: "{addr}"',
        "    connection_pool: 3",
        "    aggressive_pool: true",
        "    retry_interval: 1",
        "    dial_timeout: 5",
        "",
        "quantummux:",
    ]

    if interface:
        lines.append(f'  interface: "{interface}"')
    if local_ip:
        lines.append(f'  local_ip: "{local_ip}"')
    if router_mac:
        lines.append(f'  router_mac: "{router_mac}"')

    lines.extend(
        [
            "  mtu: 1280",
            "  snd_wnd: 1024",
            "  rcv_wnd: 1024",
            "  data_shard: 10",
            "  parity_shard: 3",
            "  ttl_base: 64",
            "  ttl_jitter: 8",
            "  tcp_window: 65535",
            "  ack_step_min: 64",
            "  ack_step_max: 512",
            '  tcp_flags: "PA"',
            "  idle_timeout: 60",
            "  icmpv6_mode: true",
            "",
            "smux:",
            "  keepalive: 8",
            "  max_recv: 8388608",
            "  max_stream: 8388608",
            "  frame_size: 32768",
            "  version: 2",
            "",
            "kcp:",
            "  nodelay: 1",
            "  interval: 10",
            "  resend: 2",
            "  nc: 1",
            "  sndwnd: 1024",
            "  rcvwnd: 1024",
            "  mtu: 1400",
            "",
            "advanced:",
            "  tcp_nodelay: true",
            "  tcp_keepalive: 15",
            "  tcp_read_buffer: 4194304",
            "  tcp_write_buffer: 4194304",
            "  websocket_read_buffer: 65536",
            "  websocket_write_buffer: 65536",
            "  websocket_compression: false",
            "  cleanup_interval: 3",
            "  session_timeout: 60",
            "  connection_timeout: 30",
            "  stream_timeout: 120",
            "  max_connections: 2000",
            "  max_udp_flows: 1000",
            "  udp_flow_timeout: 300",
            "  udp_buffer_size: 4194304",
            "",
            "obfuscation:",
            "  enabled: false",
            "  min_padding: 16",
            "  max_padding: 512",
            "  min_delay_ms: 0",
            "  max_delay_ms: 0",
            "  burst_chance: 0.15",
            "",
            "http_mimic:",
            '  fake_domain: "www.google.com"',
            '  fake_path: "/search"',
            '  user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"',
            "  chunked_encoding: false",
            "  session_cookie: true",
            "  custom_headers:",
            '    - "Accept-Language: en-US,en;q=0.9"',
            '    - "Accept-Encoding: gzip, deflate, br"',
        ]
    )

    return "\n".join(lines).strip() + "\n"


def render_client_yaml_tun_bip(addr: str, psk: str, *, dest_ip: str, health_port: int) -> str:
    addr = _yaml_escape(addr)
    psk = _yaml_escape(psk)
    dest_ip = _yaml_escape(dest_ip.strip())
    hp = int(health_port)

    lines = [
        'mode: "client"',
        f'psk: "{psk}"',
        'profile: "latency"',
        "verbose: true",
        "heartbeat: 2",
        "",
        "paths:",
        '  - transport: "tun"',
        f'    addr: "{addr}"',
        "    connection_pool: 1",
        "    retry_interval: 1",
        "    dial_timeout: 5",
        "",
        "tun_transport:",
        '  device_name: "dagger0"',
        '  local_cidr: "10.10.10.2/24"',
        '  remote_cidr: "10.10.10.1/24"',
        "  mtu: 1320",
        f"  health_port: {hp}",
        '  profile: "bip"',
        '  listen_ip: "0.0.0.0"',
        f'  dest_ip: "{dest_ip}"',
        "  auto_tuning: true",
        '  tuning_profile: "balanced"',
        "  workers: 0",
        "  batch_size: 2048",
        "",
        "smux:",
        "  keepalive: 8",
        "  max_recv: 8388608",
        "  max_stream: 8388608",
        "  frame_size: 32768",
        "  version: 2",
        "",
        "kcp:",
        "  nodelay: 1",
        "  interval: 10",
        "  resend: 2",
        "  nc: 1",
        "  sndwnd: 1024",
        "  rcvwnd: 1024",
        "  mtu: 1400",
        "",
        "advanced:",
        "  tcp_nodelay: true",
        "  tcp_keepalive: 15",
        "  tcp_read_buffer: 4194304",
        "  tcp_write_buffer: 4194304",
        "  websocket_read_buffer: 65536",
        "  websocket_write_buffer: 65536",
        "  websocket_compression: false",
        "  cleanup_interval: 3",
        "  session_timeout: 60",
        "  connection_timeout: 30",
        "  stream_timeout: 120",
        "  max_connections: 2000",
        "  max_udp_flows: 1000",
        "  udp_flow_timeout: 300",
        "  udp_buffer_size: 4194304",
        "",
        "obfuscation:",
        "  enabled: false",
        "  min_padding: 16",
        "  max_padding: 512",
        "  min_delay_ms: 0",
        "  max_delay_ms: 0",
        "  burst_chance: 0.15",
        "",
        "http_mimic:",
        '  fake_domain: "www.google.com"',
        '  fake_path: "/search"',
        '  user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"',
        "  chunked_encoding: false",
        "  session_cookie: true",
        "  custom_headers:",
        '    - "Accept-Language: en-US,en;q=0.9"',
        '    - "Accept-Encoding: gzip, deflate, br"',
    ]
    return "\n".join(lines).strip() + "\n"


def render_service_unit() -> str:
    return dedent(
        """
        [Unit]
        Description=DaggerConnect Client
        After=network.target

        [Service]
        ExecStart=/usr/local/bin/DaggerConnect -c /etc/DaggerConnect/client.yaml
        Restart=always
        RestartSec=5
        User=root

        [Install]
        WantedBy=multi-user.target
        """
    ).strip() + "\n"
