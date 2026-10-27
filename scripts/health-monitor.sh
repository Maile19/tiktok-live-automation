#!/usr/bin/env bash
# TikTok Live Automation - Autonomous Health Monitor & Self-Healing Script

LOG_FILE="/var/log/tiktok-live-automation-monitor.log"
SERVICE_NAME="tiktok-live-automation"
HEALTH_URL="http://127.0.0.1:3000/health"

log_msg() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log_msg "Starting health check..."

# 1. Check HTTP health endpoint
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$HEALTH_URL" || echo "000")

if [ "$HTTP_STATUS" -ne 200 ]; then
    log_msg "WARNING: /health endpoint returned HTTP $HTTP_STATUS. Restarting $SERVICE_NAME..."
    sudo systemctl restart "$SERVICE_NAME"
    sleep 3
    
    # Re-check
    NEW_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$HEALTH_URL" || echo "000")
    if [ "$NEW_STATUS" -eq 200 ]; then
        log_msg "SUCCESS: Service successfully recovered and /health returned 200."
    else
        log_msg "CRITICAL: Service failed to recover after restart! HTTP status: $NEW_STATUS"
    fi
else
    log_msg "OK: /health endpoint healthy (HTTP 200)."
fi

# 2. Check Disk & RAM usage
DISK_USAGE=$(df / | awk 'NR==2 {print $5}' | tr -d '%')
if [ "$DISK_USAGE" -gt 85 ]; then
    log_msg "WARNING: Disk usage is high (${DISK_USAGE}%). Cleaning old logs..."
    sudo journalctl --vacuum-time=3d
    sudo rm -rf /home/ubuntu/tiktok-live-automation/logs/*.log
fi

RAM_USAGE=$(free | awk '/Mem:/ {printf("%.0f", $3/$2 * 100)}')
if [ "$RAM_USAGE" -gt 90 ]; then
    log_msg "WARNING: RAM usage is high (${RAM_USAGE}%)."
fi

log_msg "Health check completed."
