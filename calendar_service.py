import os
from datetime import datetime, timedelta
import zoneinfo
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Definiamo la timezone italiana
ROME_TZ = zoneinfo.ZoneInfo("Europe/Rome")

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
    """Converte una stringa ISO in un datetime localizzato con fuso orario Europe/Rome."""
    clean_str = str(data_ora_iso).strip().replace("Z", "")
    if "T" not in clean_str and " " in clean_str:
        clean_str = clean_str.replace(" ", "T")
    
    parts = clean_str.split("T")
    if len(parts) == 2 and parts[1].count(":") == 1:
        clean_str = f"{parts[0]}T{parts[1]}:00"
        
    dt = datetime.fromisoformat(clean_str)
    
    # Se il datetime non ha timezone, assegniamo Europe/Rome
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ROME_TZ)
    else:
        dt = dt.astimezone(ROME_TZ)
        
    return dt

def verifica_disponibilita_calendar(service, calendar_id: str, data_ora_iso: str, durata_minuti: int = 30) -> bool:
    """
    Verifica se nell'intervallo [dt_inizio, dt_fine] ci sono eventi sovrapposti su Google Calendar.
    """
    try:
        dt_inizio = parse_iso_datetime(data_ora_iso)
        dt_fine = dt_inizio + timedelta(minutes=int(durata_minuti))

        # Estendiamo la ricerca: da 12 ore prima dell'evento fino a 12 ore dopo
        check_start = dt_inizio - timedelta(hours=12)
        check_end = dt_fine + timedelta(hours=12)

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=check_start.isoformat(),
            timeMax=check_end.isoformat(),
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])

        # Controllo matematico di sovrapposizione tra intervalli
        for event in events:
            start_raw = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
            end_raw = event.get('end', {}).get('dateTime') or event.get('end', {}).get('date')
            
            if start_raw and end_raw:
                # Gestione eventi "All day" (solo data YYYY-MM-DD)
                if len(start_raw) == 10:
                    start_raw += "T00:00:00"
                if len(end_raw) == 10:
                    end_raw += "T23:59:59"

                ev_start = parse_iso_datetime(start_raw[:19])
                ev_end = parse_iso_datetime(end_raw[:19])

                # Sovrapposizione: (NuovoInizio < FineEsistente) E (NuovaFine > InizioEsistente)
                if dt_inizio < ev_end and dt_fine > ev_start:
                    print(f"CONFLITTO TROVATO: L'evento '{event.get('summary')}' ({ev_start} - {ev_end}) blocca la richiesta ({dt_inizio} - {dt_fine})")
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
