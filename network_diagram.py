"""network_diagram: render the home-network SSOT topology (docs/network.md) as a PNG,
overlaying live UniFi online/offline status. Lightweight (Graphviz dot). No endpoints.
Topology line format: id | label | type | parent | flags | match(optional)
  flags: ssot_only (not UniFi-managed) -> neutral color, no live status
  match: optional alias substring to match against the live UniFi device name"""
import os, re, subprocess, tempfile, time

NETWORK_MD = "/opt/clawdia/docs/network.md"

_TYPE_STYLE = {
    "isp":     ("cloud",     "#dae8fc"),
    "modem":   ("box",       "#fff2cc"),
    "gateway": ("box3d",     "#d5e8d4"),
    "switch":  ("box",       "#e1d5e7"),
    "ap":      ("ellipse",   "#d5e8d4"),
    "camera":  ("component", "#ffe6cc"),
    "chime":   ("note",      "#fff2cc"),
    "power":   ("cylinder",  "#f8cecc"),
    "ups":     ("cylinder",  "#f8cecc"),
}

def _parse_topology(path=NETWORK_MD):
    nodes = []
    try:
        text = open(path).read()
    except Exception as e:
        return None, f"cannot read {path}: {e}"
    m = re.search(r"```topology\s*\n(.*?)```", text, re.S)
    if not m:
        return None, "no ```topology block in network.md"
    for line in m.group(1).strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            continue
        nid, label, ntype, parent = parts[0], parts[1], parts[2], parts[3]
        flags = parts[4] if len(parts) > 4 else ""
        match = parts[5] if len(parts) > 5 else ""
        nodes.append({"id": nid, "label": label, "type": ntype,
                      "parent": parent or None, "ssot_only": "ssot_only" in flags,
                      "match": match})
    return nodes, None

def _live_status_map():
    try:
        import unifi_client as _u
        out = {}
        for d in _u.list_devices():
            nm = (d.get("name") or "").lower()
            if nm:
                out[nm] = (d.get("status") or "").lower()
        return out
    except Exception:
        return {}

def _match_status(node, status_map):
    if not status_map:
        return None
    needles = [node["label"].lower()]
    if node.get("match"):
        needles.insert(0, node["match"].lower())
    for needle in needles:
        for nm, st in status_map.items():
            if needle in nm or nm in needle:
                return st
            ntok = set(re.findall(r"[a-z0-9]+", needle))
            nmtok = set(re.findall(r"[a-z0-9]+", nm))
            if ntok and len(ntok & nmtok) >= max(2, len(ntok) - 1):
                return st
    return None

def _esc(s):
    return s.replace('"', '\\"')

# group leaf types into ranked clusters so layout isn't one wide row
_CLUSTER = {"ap": "APs", "camera": "Cameras", "chime": "Chimes"}

def build_dot():
    nodes, err = _parse_topology()
    if err:
        return None, err
    sm = _live_status_map()
    online = offline = unknown = ssot = 0
    out = ['digraph home_network {',
           '  rankdir=TB; bgcolor="white"; pad=0.3; nodesep=0.35; ranksep=0.6;',
           '  node [fontname="Helvetica", fontsize=11, style="filled,rounded", penwidth=1.8];',
           '  edge [color="#888888", penwidth=1.2];']
    def node_line(n):
        shape, fill = _TYPE_STYLE.get(n["type"], ("box", "#eeeeee"))
        nonlocal online, offline, unknown, ssot
        tag = ""
        if n["ssot_only"]:
            stroke = "#999999"; ssot += 1
        else:
            st = _match_status(n, sm)
            if st == "online":
                stroke = "#2e7d32"; tag = "  ✓"; online += 1
            elif st == "offline":
                stroke = "#c62828"; fill = "#f8cecc"; tag = "  ✗ OFFLINE"; offline += 1
            else:
                stroke = "#e0a000"; tag = "  ?"; unknown += 1
        return f'  {n["id"]} [label="{_esc(n["label"])}{tag}", shape={shape}, fillcolor="{fill}", color="{stroke}"];'
    # backbone nodes (non-clustered)
    for n in nodes:
        if n["type"] not in _CLUSTER:
            out.append(node_line(n))
    # clustered leaf nodes
    for ctype, ctitle in _CLUSTER.items():
        members = [n for n in nodes if n["type"] == ctype]
        if not members:
            continue
        out.append(f'  subgraph cluster_{ctype} {{ label="{ctitle}"; style="rounded,dashed"; color="#bbbbbb"; fontsize=10;')
        for n in members:
            out.append("  " + node_line(n))
        out.append('  }')
    # edges
    for n in nodes:
        if n["parent"]:
            out.append(f'  {n["parent"]} -> {n["id"]};')
    out.append('}')
    return "\n".join(out), {"online": online, "offline": offline, "unknown": unknown,
                            "ssot_only": ssot, "total": len(nodes), "live": bool(sm)}

def render_png(out_path=None):
    dot, summary = build_dot()
    if dot is None:
        return None, summary
    if out_path is None:
        out_path = os.path.join(tempfile.gettempdir(), f"network_{int(time.time())}.png")
    try:
        p = subprocess.run(["dot", "-Tpng", "-Gdpi=120", "-o", out_path],
                           input=dot.encode(), capture_output=True, timeout=30)
        if p.returncode != 0:
            return None, f"dot failed: {p.stderr.decode()[:200]}"
    except Exception as e:
        return None, f"render error: {e}"
    return out_path, summary

if __name__ == "__main__":
    import sys
    os.chdir("/opt/clawdia"); sys.path.insert(0, "/opt/clawdia")
    path, summary = render_png("/tmp/network_test.png")
    print("PNG:", path); print("summary:", summary)
