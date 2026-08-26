import os
import requests

def send_email(to_email: str, subject: str, body: str) -> bool:
    api_key = os.getenv("RESEND_API_KEY")
    sender_email = os.getenv("SMTP_EMAIL", "onboarding@resend.dev")

    if not api_key:
        print("Errore: RESEND_API_KEY non trovata nelle variabili d'ambiente.")
        return False

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "from": f"Bot <{sender_email}>",
        "to": [to_email],
        "subject": subject,
        "html": body
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code in [200, 201]:
            print(f"Email inviata con successo a {to_email} via Resend API!")
            return True
        else:
            print(f"Errore Resend API [{response.status_code}]: {response.text}")
            return False
    except Exception as e:
        print(f"Errore durante la chiamata API di invio email: {e}")
        return False
