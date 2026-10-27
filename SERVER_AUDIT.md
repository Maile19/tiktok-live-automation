# Server Audit Report: TikTok Live Automation & Polya AI Isolation

## Overview
This document audits the physical and logical resource allocation, system isolation boundaries, and security hardening measures implemented on the server hosting both Polya AI and the TikTok Live Automation engine.

## Resource Isolation & Constraints
To ensure the TikTok Live Automation engine does not starve or interfere with Polya AI core services, strict systemd cgroup resource limits are enforced:
- **Service Name**: `tiktok-live-automation.service`
- **Memory Limit (`MemoryMax`)**: 512MB (Prevents memory leaks from consuming host RAM required by Polya AI models).
- **CPU Quota (`CPUQuota`)**: 50% of 1 CPU core (Limits CPU spikes during heavy video processing or high-frequency gift traffic).
- **Restart Policy**: `Restart=on-failure`, `RestartSec=5s`.

## Security & Secrets Management
- **GitHub Token**: Configured via secure environment injection / git remote auth (`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`).
- **Credential Isolation**: `.env` is strictly excluded via `.gitignore` and is absent from Git history.
- **Local Binding**: The HTTP health and simulation API binds strictly to loopback (`127.0.0.1:3000`), preventing external exposure.

## Storage & Log Rotation
- **Disk Monitoring**: Automated via `health-monitor.sh` (runs every 5 minutes via cron).
- **Log Management**: Systemd journal logs and temporary media files are automatically purged or rotated when disk usage exceeds 85%.
