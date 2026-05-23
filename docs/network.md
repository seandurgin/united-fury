# Home Network Topology (SSOT)

Source of truth for Sean's home network physical/logical topology, used by the
`network_diagram` tool. The diagram renders this STRUCTURE and overlays LIVE UniFi
status (online/offline) on each node that UniFi manages. Nodes marked `ssot_only`
are not visible to UniFi (e.g. the UPS, the ISP) and render in a neutral color.

Endpoints/clients (phones, laptops, Tailnet devices) are intentionally NOT included
— this is infrastructure only.

Last verified against live UniFi inventory: 2026-05-23 (14 devices, all online).

## Topology

Format: each line is `id | label | type | parent_id | flags`
- type: isp | modem | gateway | switch | ap | camera | chime | power | ups
- parent_id: the id this device uplinks to (blank for root)
- flags: `ssot_only` if not managed by UniFi (won't get a live-status color)

```topology
isp        | Internet (ISP)              | isp     |          | ssot_only
modem      | UCI Cable Internet          | modem   | isp      |
udmse      | UDM SE (Gateway/Controller) | gateway | modem    |          | dream machine
switch     | USW Pro Max 24 PoE          | switch  | udmse    |
ap_max     | U7 Pro Max                  | ap      | switch   |
ap_wall    | U7 Pro Wall                 | ap      | switch   |
ap_pro     | U7 Pro                      | ap      | switch   |
ap_out     | U7 Pro Outdoor              | ap      | switch   |
cam_door   | G4 Doorbell Pro PoE         | camera  | switch   |
cam_turret | G5 Turret Ultra            | camera  | switch   |
cam_g6     | G6 Instant                  | camera  | switch   |
chime_bsmt | Basement Chime              | chime   | switch   |
chime_ff   | First Floor PoE Chime       | chime   | switch   |
chime_up   | Upstairs Chime              | chime   | switch   |
pdu        | USP PDU Pro                 | power   | switch   |          | power distribution
ups        | UPS 2U                      | ups     | switch   | ssot_only
```

## Notes
- The UPS (`ups`) protects the rack and is not a UniFi-managed device; adjust its
  parent if it actually feeds the UDM/switch rather than hanging off the switch.
- WAN is dual-configured (2 WANs per UniFi). Only the primary ISP path is drawn.
- To match UniFi live status, the `label` here is matched against UniFi device
  names by fuzzy contains; if a node never lights up green, tweak its label to
  better match the UniFi device name.
