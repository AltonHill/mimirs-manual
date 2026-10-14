# 16 — InfiniBand / GPU Fabric (Host Level)

> **Prerequisites:** 03 (nodes provisioned). Do this when the site has
> an InfiniBand fabric (DGX H200/B200/B300 ship NVIDIA Quantum NDR IB).
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
ibstat | grep -E 'CA type|State|Rate|Link layer'
# expect per port: State: Active, Rate: 400 (NDR), Link layer: InfiniBand
ibv_devinfo | grep -E 'hca_id|fw_ver|node_guid'
```

- [ ] Every IB port `Active` at the expected rate (NDR = 400 Gb/s)
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
cmsh -c "device; use <gpu-node>; interfaces; add physical ib0 gpu-ib; set ip <ip>; commit"
```

Validate:

```bash
PEER_IB_IP="<peer-ib-ip>"   # peer node's ib0 address, from your 01 plan
ip -br addr show ib0
ping -c3 "$PEER_IB_IP"
```

## 16.5 — Fabric bandwidth check

Between two GPU nodes (validates the full path, not just link state):

```bash
NODE_A_IB_IP="<node-a-ib-ip>"   # node A's ib0 address, from your 01 plan
# node A (server):
ib_write_bw -d mlx5_0
# node B (client):
ib_write_bw -d mlx5_0 "$NODE_A_IB_IP"
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
