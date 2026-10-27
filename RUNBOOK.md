# Runbook: TikTok Live Automation Operations

## Service Management
- **Start service**: `sudo systemctl start tiktok-live-automation`
- **Stop service**: `sudo systemctl stop tiktok-live-automation`
- **Check status**: `sudo systemctl status tiktok-live-automation`
- **View logs**: `sudo journalctl -u tiktok-live-automation -f`

## Health & Monitoring
- **Check health API**: `curl http://127.0.0.1:3000/health`
- **Simulate gift event**:
  ```bash
  curl -X POST http://127.0.0.1:3000/simulate-gift \
       -H "Content-Type: application/json" \
       -d '{"userId": "test_user_99", "giftId": "gift_01_id"}'
  ```

## Autonomous Monitoring & Cron
- Health monitor script located at `/home/ubuntu/tiktok-live-automation/scripts/health-monitor.sh`.
- Scheduled in crontab to run every 5 minutes.
- Cron logs written to `/var/log/tiktok-live-automation-monitor.log`.
