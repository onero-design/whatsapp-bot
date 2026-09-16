import os
import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

def get_calendar_service(access_token: str, refresh_token: str, client_id: str, client_secret: str):
    """Crea e restituisce il client autenticato per l'API di Google Calendar."""
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret
    )
    return build('calendar', 'v3', credentials=creds)

def aggiungi_evento_calendar(service, calendar_id: str, sommario: str, descrizione: str, inizio_iso: str, durata_minuti: int = 30) -> str:
    """Inserisce un evento nel Google Calendar specificato."""
    try:
        start_time = datetime.datetime.fromisoformat(inizio_iso)
        end_time = start_time + datetime.timedelta(minutes=durata_minuti)

        event = {
            'summary': sommario,
            'description': descrizione,
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': 'Europe/Rome',
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'Europe/Rome',
            },
        }

        created_event = service.events().insert(calendarId=calendar_id, body=event).execute()
        return f"Evento creato con successo su Google Calendar ID: {created_event.get('id')}"
    except Exception as e:
        return f"Errore durante la creazione dell'evento su Google Calendar: {str(e)}"
