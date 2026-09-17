import os
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

def get_calendar_service(access_token: str, refresh_token: str, client_id: str, client_secret: str):
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret
    )
    return build("calendar", "v3", credentials=creds)

def parse_to_iso_with_tz(data_ora_iso: str) -> str:
    """Garantisce il formato ISO8601 corretto con offset fuso orario italiano (+02:00 / +01:00)."""
    clean_str = str(data_ora_iso).strip().replace("Z", "")
    if "T" not in clean_str and " " in clean_str:
        clean_str = clean_str.replace(" ", "T")
    
    parts = clean_str.split("T")
    if len(parts) == 2 and parts[1].count(":") == 1:
        clean_str = f"{parts[0]}T{parts[1]}:00"
    
    # Prendiamo solo YYYY-MM-DDTHH:MM:SS ignorando eventuali vecchi offset
    base_iso = clean_str[:19]
    dt = datetime.fromisoformat(base_iso)
    
    # Calcolo se siamo in ora legale (+02:00) o solare (+01:00) in Italia
    # In Settembre siamo in Ora Legale (+02:00)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "+02:00"

def verifica_disponibilita_calendar(service, calendar_id: str, data_ora_iso: str, durata_minuti: int = 30) -> bool:
    try:
        iso_inizio = parse_to_iso_with_tz(data_ora_iso)
        dt_inizio = datetime.fromisoformat(iso_inizio)
        dt_fine = dt_inizio + timedelta(minutes=int(durata_minuti))
        iso_fine = dt_fine.strftime("%Y-%m-%dT%H:%M:%S") + "+02:00"

        # Finestra di controllo ampia attorno all'orario richiesto
        check_start = (dt_inizio - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S") + "+02:00"
        check_end = (dt_fine + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S") + "+02:00"

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=check_start,
            timeMax=check_end,
            singleEvents=True,
            timeZone='Europe/Rome',
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])

        for event in events:
            start_raw = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
            end_raw = event.get('end', {}).get('dateTime') or event.get('end', {}).get('date')
            
            if start_raw and end_raw:
                if len(start_raw) == 10: start_raw += "T00:00:00+02:00"
                if len(end_raw) == 10: end_raw += "T23:59:59+02:00"

                ev_start = datetime.fromisoformat(start_raw)
                ev_end = datetime.fromisoformat(end_raw)

                # Sovrapposizione: (NuovoInizio < FineEsistente) E (NuovaFine > InizioEsistente)
                if dt_inizio < ev_end and dt_fine > ev_start:
                    return False # Occupato!

        return True # Libero!
    except Exception as e:
        print(f"Errore verifica_disponibilita_calendar: {e}")
        return True

def inserisci_evento_calendar(service, calendar_id: str, summary: str, description: str, data_ora_iso: str, durata_minuti: int = 30):
    iso_inizio = parse_to_iso_with_tz(data_ora_iso)
    dt_inizio = datetime.fromisoformat(iso_inizio)
    dt_fine = dt_inizio + timedelta(minutes=int(durata_minuti))
    iso_fine = dt_fine.strftime("%Y-%m-%dT%H:%M:%S") + "+02:00"

    event = {
        'summary': summary,
        'description': description,
        'start': {
            'dateTime': iso_inizio,
            'timeZone': 'Europe/Rome',
        },
        'end': {
            'dateTime': iso_fine,
            'timeZone': 'Europe/Rome',
        },
    }

    return service.events().insert(calendarId=calendar_id, body=event).execute()
