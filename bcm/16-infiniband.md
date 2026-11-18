# 16 — InfiniBand / GPU Fabric (Host Level)

> **Prerequisites:** 03 (nodes provisioned). Do this when the site has
> an InfiniBand compute fabric. The rate a healthy port negotiates
> depends on the **device class** — see 16.1b; there is no single
> fleet-wide number.
>
> **Sourcing note:** neither source guide covered IB host setup — this
> section is standard DGX/BCM practice. Validate each step against the
> site's fabric before treating it as gospel.

## 16.1 — What lives where

| Piece | Where it runs | Notes |
|---|---|---|
| Subnet Manager (`opensm`) | IB switches (managed) | If the switches are unmanaged, run `opensm` on one host per fabric |
| OFED drivers | Every GPU node (in the image) | DGX OS ships MLNX_OFED; BCM images need it too |
| IPoIB (`ib0`) | GPU nodes, if the site routes IP over IB | Optional — only if `gpu-ib` carries IP traffic |

If the site also runs a GPU east-west **Ethernet** fabric, it's just
another row in `networks.csv` (`gpu-eth`) — no special host config.

## 16.1b — Expected rate per device class

From NVIDIA's DGX user guides (verified 2026-11-17):

| System | Compute-fabric NICs | Per-port rate | Source |
|---|---|---|---|
| DGX H100 / H200 | 8× ConnectX-7 single-port | up to 400 Gb/s InfiniBand (NDR) | [DGX H100/H200 User Guide](https://docs.nvidia.com/dgx/dgxh100-user-guide/introduction-to-dgxh100.html) |
| DGX B200 | 8× ConnectX-7 single-port | up to 400 Gb/s InfiniBand (NDR) | [DGX B200 User Guide](https://docs.nvidia.com/dgx/dgxb200-user-guide/introduction-to-dgxb200.html) |
| DGX B300 | 8× ConnectX-8 | 800 Gb/s InfiniBand/Ethernet (XDR) | [DGX B300 User Guide](https://docs.nvidia.com/dgx/dgxb300-user-guide/introduction-to-dgxb300.html) |

The storage/in-band-management NICs are a different class (BlueField-3
on B200/B300, up to 400 Gb/s; ConnectX-7 dual-port on H100/H200) — judge
them against their own row of the site's design, never the compute
fabric's. A ConnectX-8 can also be configured for XDR Lite (400 Gb/s)
per the [ConnectX-8 SuperNIC user
manual](https://docs.nvidia.com/networking/display/nvidia-connectx-8-supernic-user-manual.pdf):
if the site's fabric is NDR (Quantum-2) or the design says XDR Lite,
400 is the expected rate for those ports — record that in 10. Mixed
fleets: one expected rate per host type, like one category per type.
**[unverified]** the exact `ibstat` rate string for an XDR port
(expected `Rate: 800`) — confirm on the first B300 and record it.

## 16.2 — OFED in the software image

```bash
cm-chroot-sw-img /cm/images/$IMG_GPU
ofed_info -s          # expect: MLNX_OFED version string
ibstat | head -20     # expect: CA 'mlx5_0', State: Active (after SM is up)
exit
```

If OFED is missing from the image, install the MLNX_OFED package for
the image's kernel (`$KERNEL_VER`) before first provisioning — IB won't
come up without it, and there's no graceful fallback.

## 16.3 — Subnet manager

On managed IB switches the SM is already running. Otherwise, pick one
host per fabric:

```bash
# on the SM host:
systemctl enable --now opensm
```

Validate from any GPU node:

```bash
ibstat | grep -E '^CA |CA type|State|Rate|Link layer'
# expect per compute-fabric port: State: Active, Link layer: InfiniBand,
# Rate = the 16.1b rate for THIS host type (ConnectX-7: 400; ConnectX-8: 800,
# or 400 where the design says NDR / XDR Lite). A port below its class's
# rate negotiated down (cable, optic, switch port) — that is a FAIL.
ibv_devinfo | grep -E 'hca_id|fw_ver|node_guid'
```

- [ ] Every compute-fabric port `Active` at its device class's expected
      rate (16.1b) — checked per host type, never against one fleet number
- [ ] Storage/management ports judged against their own class, not the
      compute fabric's
- [ ] One SM visible per fabric (`ibstat` shows `SM lid`)

## 16.3b — UFM servers (when the site runs UFM)

Sites with a managed Quantum fabric usually run NVIDIA UFM on a pair of
dedicated servers instead of (or alongside) switch-resident SMs.

- **UFM servers are physical — never VMs.** UFM needs real IB HCAs; a VM
  has no fabric presence. Do not let a virtualization plan absorb them.
- **Provision them like GPU nodes.** Give the UFM pair entries in
  `inventory.csv`, their own category and image, and let BCM install
  them like everything else — they run no GPU workloads, but they are
  first-class nodes in the build. Fabric services (SM, UFM) come up
  with the image; no hand-built exceptions.
- **The head node has no fabric visibility** — doubly true when it is a
  VM (the standard topology). IB is configured "in the blind" from
  `cmsh` across the provisioning network; fabric state is read back
  from UFM or from the GPU nodes (`ibstat`, `ibv_devinfo`, §16.5),
  never from the head node. If a validation step seems to require IB
  access from the head node, it is written wrong — move it to a node.

## 16.4 — IPoIB (only if the site uses it)

If `gpu-ib` carries IP traffic, each GPU node needs an `ib0` interface
on that network:

```bash
# per GPU node: NODE and NODE_IB_IP from your 01 plan
cmsh -c "device; use ${NODE:?set NODE to the GPU node}; interfaces; add physical ib0 gpu-ib; set ip ${NODE_IB_IP:?set NODE_IB_IP to its ib0 address}; commit"
```

Validate:

```bash
# PEER_IB_IP: the peer node's ib0 address, from your 01 plan
ip -br addr show ib0
ping -c3 "${PEER_IB_IP:?set PEER_IB_IP to the peer ib0 address}"
```

## 16.5 — Fabric bandwidth check

Between two GPU nodes (validates the full path, not just link state):

```bash
# NODE_A_IB_IP: node A's ib0 address, from your 01 plan
# node A (server):
ib_write_bw -d mlx5_0
# node B (client):
ib_write_bw -d mlx5_0 "${NODE_A_IB_IP:?set NODE_A_IB_IP to node A ib0 address}"
# expect: ~line rate for the HCA generation
```

- [ ] Bandwidth within ~90% of the HCA's rated line rate
- [ ] No port errors accumulating: `ibstat` `Phys state: LinkUp`, check
      `port_rcv_errors` / `port_xmit_discards` via `perfquery`

## Troubleshooting

- **Port `Down`/`Initializing`:** check the cable and the switch port;
  then check exactly one SM is running per fabric (two SMs fight).
- **`ibstat` shows no devices:** OFED didn't load — `lsmod | grep mlx5_ib`,
  check the image kernel matches the OFED build.
- **IPoIB up but no traffic:** verify the `gpu-ib` network exists in BCM
  (`cmsh -c "network; list"`) and the interface is on the right network.
