# Google Cloud Deploy (Teacher OTP)

Use this for deploying `exam_backend` on Google Cloud Run with TextBee OTP enabled.

## 1) Required Environment Variables

- `MONGO_URL`
- `STUDENT_MONGO_URI`
- `TEXTBEE_API_URL`
- `TEXTBEE_API_KEY`
- `TEXTBEE_DEVICE_ID`

Optional:
- `TEXTBEE_TIMEOUT_SEC` (default `4`)
- `TEXTBEE_MAX_ATTEMPTS` (default `12`)

Recommended TextBee endpoint format:

`https://api.textbee.dev/api/v1/gateway/devices/<YOUR_DEVICE_ID>/send-sms`

## 2) Deploy Command (Cloud Run)

Run from `EXAM_BACKEND/exam_backend`:

```bash
gcloud run deploy exam-backend \
  --source . \
  --platform managed \
  --region asia-south1 \
  --allow-unauthenticated \
  --set-env-vars MONGO_URL="YOUR_EXAM_DB_URI",STUDENT_MONGO_URI="YOUR_STUDENT_DB_URI",TEXTBEE_API_URL="https://api.textbee.dev/api/v1/gateway/devices/YOUR_DEVICE_ID/send-sms",TEXTBEE_API_KEY="YOUR_TEXTBEE_API_KEY",TEXTBEE_DEVICE_ID="YOUR_DEVICE_ID",TEXTBEE_TIMEOUT_SEC="4",TEXTBEE_MAX_ATTEMPTS="12"
```

## 3) Verify After Deploy

Replace `<BASE_URL>` with Cloud Run service URL:

```bash
curl "<BASE_URL>/teacher/auth/otp/config-check"
curl "<BASE_URL>/teacher/auth/profile?username=VANSH"
```

Expected:
- `config.ok` should be `true`
- profile route should return teacher data instead of `404`

