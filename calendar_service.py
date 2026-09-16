import os
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

def get_calendar_service(access_token: str, refresh_token: str, client_id: str, client_secret: str):
    """Crea e restituisce il servizio Google Calendar con credenziali aggiornate."""
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret
    )
    return build("calendar", "v3", credentials=creds)

def parse_iso_datetime(data_ora_iso: str) -> datetime:
    clean_str = data_ora_iso.strip().replace("Z", "")
    if "T" not in clean_str and " " in clean_str:
        clean_str = clean_str.replace(" ", "T")
    
    parts = clean_str.split("T")
    if len(parts) == 2 and parts[1].count(":") == 1:
        clean_str = f"{parts[0]}T{parts[1]}:00"
        
    return datetime.fromisoformat(clean_str)

def verifica_disponibilita_calendar(service, calendar_id: str, data_ora_iso: str, durata_minuti: int = 30) -> bool:
    """
    Verifica se nella finestra di tempo selezionata ci sono eventi sovrapposti su Google Calendar.
    Restituisce True se è LIBERO, False se è OCCUPATO.
    """
    try:
        dt_inizio = parse_iso_datetime(data_ora_iso)
        dt_fine = dt_inizio + timedelta(minutes=durata_minuti)

        # Formattazione ISO con offset (es. Italia/Europa)
        time_min = dt_inizio.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        time_max = dt_fine.strftime("%Y-%m-%dT%H:%M:%S") + "Z"

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])
        return len(events) == 0
    except Exception as e:
        print(f"Errore verifica_disponibilita_calendar: {e}")
        # In caso di dubbio o errore di connessione a Google, assumiamo libero lasciando fare il controllo al DB
        return True

def inserisci_evento_calendar(service, calendar_id: str, summary: str, description: str, data_ora_iso: str, durata_minuti: int = 30):
    """
    Inserisce un evento su Google Calendar.
    """
    dt_inizio = parse_iso_datetime(data_ora_iso)
    dt_fine = dt_inizio + timedelta(minutes=durata_minuti)

    event = {
        'summary': summary,
        'description': description,
        'start': {
            'dateTime': dt_inizio.isoformat(),
            'timeZone': 'Europe/Rome',
        },
        'end': {
            'dateTime': dt_fine.isoformat(),
            'timeZone': 'Europe/Rome',
        },
    }

    return service.events().insert(calendarId=calendar_id, body=event).execute()
