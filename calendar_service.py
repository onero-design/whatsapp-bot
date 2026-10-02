import os
from datetime import datetime, timedelta, timezone
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
    """Garantisce il formato ISO8601 corretto per le chiamate API di Google Calendar."""
    clean_str = str(data_ora_iso).strip().replace("Z", "")
    if "T" not in clean_str and " " in clean_str:
        clean_str = clean_str.replace(" ", "T")
    
    parts = clean_str.split("T")
    if len(parts) == 2 and parts[1].count(":") == 1:
        clean_str = f"{parts[0]}T{parts[1]}:00"
    
    return clean_str[:19]

def verifica_disponibilita_calendar(service, calendar_id: str, data_ora_iso: str, durata_minuti: int = 30) -> bool:
    try:
        iso_inizio = parse_to_iso_with_tz(data_ora_iso)
        dt_inizio = datetime.fromisoformat(iso_inizio)
        dt_fine = dt_inizio + timedelta(minutes=int(durata_minuti))

        # Finestra di controllo in formato ISO UTC con Z finale
        time_min = (dt_inizio - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        time_max = (dt_fine + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            timeZone='Europe/Rome',
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])

        for event in events:
            start_raw = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
            end_raw = event.get('end', {}).get('dateTime') or event.get('end', {}).get('date')
            
            if start_raw and end_raw:
                if len(start_raw) == 10:
                    start_raw += "T00:00:00"
                if len(end_raw) == 10:
                    end_raw += "T23:59:59"

                ev_start = datetime.fromisoformat(start_raw[:19])
                ev_end = datetime.fromisoformat(end_raw[:19])

                if dt_inizio < ev_end and dt_fine > ev_start:
                    return False # Occupato

        return True # Libero
    except Exception as e:
        print(f"Errore verifica_disponibilita_calendar: {e}")
        return True

def inserisci_evento_calendar(service, calendar_id: str, summary: str, description: str, data_ora_iso: str, durata_minuti: int = 30):
    iso_inizio = parse_to_iso_with_tz(data_ora_iso)
    dt_inizio = datetime.fromisoformat(iso_inizio)
    dt_fine = dt_inizio + timedelta(minutes=int(durata_minuti))

    event = {
        'summary': summary,
        'description': description,
        'start': {
            'dateTime': dt_inizio.strftime("%Y-%m-%dT%H:%M:%S"),
            'timeZone': 'Europe/Rome',
        },
        'end': {
            'dateTime': dt_fine.strftime("%Y-%m-%dT%H:%M:%S"),
            'timeZone': 'Europe/Rome',
        },
    }

    return service.events().insert(calendarId=calendar_id, body=event).execute()

def cancella_evento_calendar(service, calendar_id: str, data_ora_iso: str):
    """Cerca ed elimina un evento da Google Calendar considerando l'offset italiano."""
    try:
        iso_inizio = parse_to_iso_with_tz(data_ora_iso)
        dt_inizio = datetime.fromisoformat(iso_inizio)
        
        # Tolleranza di 45 minuti prima e dopo
        dt_min = dt_inizio - timedelta(minutes=45)
        dt_max = dt_inizio + timedelta(minutes=45)
        
        # Formattazione con offset +02:00 per l'Italia (evita la Z di UTC)
        time_min = dt_min.strftime("%Y-%m-%dT%H:%M:%S+02:00")
        time_max = dt_max.strftime("%Y-%m-%dT%H:%M:%S+02:00")

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            timeZone='Europe/Rome'
        ).execute()

        events = events_result.get("items", [])
        if not events:
            print(f"Nessun evento trovato su Google Calendar tra {time_min} e {time_max}")
            return False

        for event in events:
            service.events().delete(calendarId=calendar_id, eventId=event['id']).execute()
            print(f"Evento eliminato con successo da Google Calendar: {event['id']}")
            return True

        return False
    except Exception as e:
        print(f"Errore durante la cancellazione su Google Calendar: {e}")
        return False
