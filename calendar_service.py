import os
import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

def get_calendar_service(access_token: str, refresh_token: str, client_id: str, client_secret: str):
    """Crea il client autenticato per Google Calendar."""
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret
    )
    return build('calendar', 'v3', credentials=creds)

def verifica_disponibilita_calendar(service, calendar_id: str, inizio_iso: str, durata_minuti: int = 30) -> bool:
    """Controlla se ci sono sovrapposizioni sul Google Calendar."""
    try:
        start_dt = datetime.datetime.fromisoformat(inizio_iso)
        end_dt = start_dt + datetime.timedelta(minutes=durata_minuti)

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=start_dt.isoformat() + 'Z',
            timeMax=end_dt.isoformat() + 'Z',
            singleEvents=True
        ).execute()

        events = events_result.get('items', [])
        return len(events) == 0  # True se è libero, False se c'è un impegno
    except Exception as e:
        print(f"Errore lettura Calendar: {e}")
        return False

def inserisci_evento_calendar(service, calendar_id: str, sommario: str, descrizione: str, inizio_iso: str, durata_minuti: int = 30) -> str:
    """Aggiunge l'appuntamento sul Google Calendar."""
    try:
        start_dt = datetime.datetime.fromisoformat(inizio_iso)
        end_dt = start_dt + datetime.timedelta(minutes=durata_minuti)

        event = {
            'summary': sommario,
            'description': descrizione,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Europe/Rome'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Europe/Rome'},
        }

        created_event = service.events().insert(calendarId=calendar_id, body=event).execute()
        return f"OK: Evento creato con ID {created_event.get('id')}"
    except Exception as e:
        return f"Errore creazione evento: {str(e)}"
