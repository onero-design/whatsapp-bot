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

def parse_iso_datetime(data_ora_iso: str) -> datetime:
    clean_str = str(data_ora_iso).strip().replace("Z", "")
    if "T" not in clean_str and " " in clean_str:
        clean_str = clean_str.replace(" ", "T")
    
    parts = clean_str.split("T")
    if len(parts) == 2 and parts[1].count(":") == 1:
        clean_str = f"{parts[0]}T{parts[1]}:00"
        
    return datetime.fromisoformat(clean_str)

def verifica_disponibilita_calendar(service, calendar_id: str, data_ora_iso: str, durata_minuti: int = 30) -> bool:
    """
    Verifica se nell'intervallo [dt_inizio, dt_fine] ci sono eventi sovrapposti su Google Calendar.
    Controlla anche un margine precedente per evitare di sovrapporsi ad appuntamenti già iniziati.
    """
    try:
        dt_inizio = parse_iso_datetime(data_ora_iso)
        dt_fine = dt_inizio + timedelta(minutes=int(durata_minuti))

        # Estendiamo la ricerca a ritroso (es. -2 ore) per intercettare eventi già in corso
        check_start = dt_inizio - timedelta(hours=2)
        
        time_min = check_start.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        time_max = dt_fine.strftime("%Y-%m-%dT%H:%M:%S") + "Z"

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])

        # Controllo matematico di sovrapposizione intervalli [A, B] e [C, D]
        for event in events:
            start_str = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
            end_str = event.get('end', {}).get('dateTime') or event.get('end', {}).get('date')
            
            if start_str and end_str:
                ev_start = parse_iso_datetime(start_str[:19])
                ev_end = parse_iso_datetime(end_str[:19])

                # Se (InizioNuovo < FineEsistente) E (FineNuova > InizioEsistente) -> Sovrapposizione!
                if dt_inizio < ev_end and dt_fine > ev_start:
                    return False # Occupato!

        return True # Libero!
    except Exception as e:
        print(f"Errore verifica_disponibilita_calendar: {e}")
        return True

def inserisci_evento_calendar(service, calendar_id: str, summary: str, description: str, data_ora_iso: str, durata_minuti: int = 30):
    dt_inizio = parse_iso_datetime(data_ora_iso)
    dt_fine = dt_inizio + timedelta(minutes=int(durata_minuti))

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
