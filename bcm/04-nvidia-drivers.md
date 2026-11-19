# 04 — NVIDIA Drivers

> **Prerequisites:** 03 complete (GPU image exists).

## 4.1 — Install / reinstall the driver stack (inside the GPU image)

```bash
cm-chroot-sw-img /cm/images/$IMG_GPU
apt update
apt install --reinstall -y \
  nvidia-driver-580-server \
  nvidia-utils-580-server \
  cm-nvidia-container-toolkit
```

> Do not mix the 580-server branch with 580 non-server or 610 packages.
> FabricManager and IMEX must match the driver branch exactly: install the
> branch-suffixed packages (`nvidia-fabricmanager-580`, `nvidia-imex-580`),
> never the bare `nvidia-fabricmanager` / `nvidia-imex` — the bare names
> track the newest branch (620 as of this writing), and a mismatched
> FabricManager will not initialize the NVSwitch fabric.
> Record package versions before removing anything.

> **Kernel-module flavor — Blackwell requires the OPEN modules (Rayburn,
> 2026-11-18).** The package above (`nvidia-driver-580-server`) is the
> *proprietary* flavor. On Blackwell GPUs (B200/B300; B300 = PCI ID
> 10de:2bb5) the proprietary module loads but refuses to initialize every
> GPU, and `nvidia-smi` reports "No devices were found":
>
> ```text
> NVRM: The NVIDIA GPU 0001:40:00.0 (PCI ID: 10de:2bb5) installed in this
> system requires use of the NVIDIA open kernel modules.
> NVRM: GPU 0001:40:00.0: RmInitAdapter failed! (0x22:0x56:897)
> ```
>
> For a Blackwell image, install the open flavor instead:
> `nvidia-driver-580-server-open` (same branch, `-open` suffix), with the
> same branch-matched `nvidia-fabricmanager-580` / `nvidia-imex-580`.
> Pre-Blackwell images keep the proprietary flavor. Never mix flavors in
> one image.

Configure the container toolkit for Kubernetes, enable persistence:

```bash
nvidia-ctk runtime configure --runtime=containerd
systemctl enable nvidia-fabricmanager nvidia-persistenced nvidia-dcgm
exit
```

## 4.2 — Repair: EGL overwrite conflict **[client]**

The client hit this during install:

```text
libnvidia-gl-580-server tried to overwrite
/usr/lib/x86_64-linux-gnu/libnvidia-egl-gbm.so.1.1.3
owned by libnvidia-egl-gbm1
```

Inspect first, then remove only the conflicting standalone EGL packages:

```bash
cm-chroot-sw-img /cm/images/$IMG_GPU
dpkg -S /usr/lib/x86_64-linux-gnu/libnvidia-egl-gbm.so.1.1.3
dpkg --audit
dpkg --remove --force-depends \
  libnvidia-egl-gbm1:amd64 \
  libnvidia-egl-xcb1:amd64 \
  libnvidia-egl-xlib1:amd64
apt-get --fix-broken install --no-install-recommends
dpkg --configure -a
apt-get check && dpkg --audit
exit
```

Do **not** use `dpkg --force-overwrite` as the normal repair — it leaves
two packages claiming the same files.

## 4.3 — Verify package consistency

```bash
cm-chroot-sw-img /cm/images/$IMG_GPU
dpkg -l | grep -E 'nvidia-driver|nvidia-dkms|nvidia-kernel|nvidia-firmware|libnvidia-(common|compute|gl|egl)|fabricmanager|cm-nvidia'
# every package should be on the chosen branch (580-server here)
dpkg -l | grep -E 'nvidia|libnvidia' | grep -v '580\.'
# must print nothing — any output is an off-branch straggler (e.g. a bare
# fabricmanager/imex at the newest branch); remove it before the push
dpkg -l | grep -i nvidia | grep -i open
# Blackwell images: the -open kernel package must appear here.
# Pre-Blackwell images: it must not.
dpkg -l 'linux-image-*' | awk '$1 == "ii" {print $2, $3}'
exit
```

Pin the image to the installed kernel **only after confirming it exists**:

```bash
cmsh -c "softwareimage; use $IMG_GPU; set kernelversion $KERNEL_VER; commit"
cmsh -c "softwareimage list"
```

## 4.4 — Push the image and validate on hardware

An **interactive** `cmsh` session — start it with `cmsh`, then type at
its prompts (one node at a time; `gpu-worker-1` stands for the node
you are updating):

```text
[headnode]% device
[headnode->device]% imageupdate -n gpu-worker-1 -w
```

> If a node-local repair must be retained, `grabimage` the node back
> into the image — only after reviewing exactly what will be captured.

On the GPU node itself:

```bash
nvidia-smi
# Expect: driver 580.95.05, CUDA 13.0 (record actuals for the B300 stack)
modinfo nvidia | grep -E '^(version|license):'
# Blackwell: license must read "Dual MIT/GPL" (the open module).
# "NVIDIA" means the proprietary module is loaded — on B300 the GPUs
# never initialize, however healthy the package list looked.
systemctl is-active nvidia-fabricmanager
# active, and its version matches the driver branch
nvcc --version
nvidia-container-cli info
```

- [ ] `nvidia-smi` shows all GPUs, driver/CUDA versions match the plan
- [ ] Kernel-module flavor matches the GPU generation (open on Blackwell — the `modinfo` license check above)
- [ ] `nvidia-container-cli info` reports the expected GPU model
- [ ] No `rc` (residual-config) nvidia packages remain: `dpkg -l | awk '$1=="rc" && $2~/nvidia/'`
