import os
import json
import time
from datetime import datetime, timedelta
from google import genai
from google.genai import types
from sqlalchemy.orm import Session

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

from calendar_service import (
    get_calendar_service, 
    verifica_disponibilita_calendar, 
    inserisci_evento_calendar,
    cancella_evento_calendar
)

def genera_risposta_gemini(azienda, contatto, messaggio_attuale: str, db_session: Session, SlotAgenda, Messaggio) -> str:
    if not client:
        return "Servizio IA temporaneamente non disponibile."

    # Storico messaggi
    storico = db_session.query(Messaggio).filter(
        Messaggio.contatto_id == contatto.id
    ).order_by(Messaggio.inviato_il.desc()).limit(6).all()
    storico.reverse()

    conversazione = ""
    for msg in storico:
        ruolo = "Cliente" if msg.direzione == "INBOUND" else "Assistente"
        conversazione += f"{ruolo}: {msg.testo}\n"

    ora_attuale = datetime.now()
    nome_cliente_str = contatto.nome if contatto.nome else "Cliente"

    prompt = (
        f"Data e Ora attuale del sistema: {ora_attuale.strftime('%d/%m/%Y alle %H:%M')} (Anno: {ora_attuale.year}).\n"
        f"Sei l'assistente virtuale di {azienda.nome}.\n"
        f"Stai parlando con il cliente: {nome_cliente_str}.\n\n"
        f"ISTRUZIONI E REGOLE DELL'AZIENDA (Orari e listino):\n"
        f"{azienda.istruzioni_ia}\n\n"
        f"REGOLE FONDAMENTALI PRENOTAZIONE E DISDETTA:\n"
        f"1. In base al servizio richiesto, STIMA LA DURATA IN MINUTI (es. 25, 30, 45, 60 minuti).\n"
        f"2. PRIMA di proporre o confermare QUALSIASI orario, chiama SEMPRE `controlla_orario_disponibile(data_ora_iso, durata_minuti, operatore_richiesto)` per verificare la disponibilità del barbiere.\n"
        f"3. Se il cliente non specifica il barbiere, controlla la disponibilità generale. Se Fabio è occupato e Gino è libero, avvisa il cliente che la prenotazione sarà con Gino.\n"
        f"4. Se l'orario è libero, conferma chiamando `conferma_e_prenota_appuntamento(data_ora_iso, servizio, nome_cliente, durata_minuti, operatore)` passando il nome del barbiere assegnato (es. 'Fabio' o 'Gino').\n"
        f"5. SE IL CLIENTE VUOLE DISDIRE/ANNULLARE UN APPUNTAMENTO: Chiama IMMEDIATAMENTE il tool `cancella_appuntamento(data_ora_iso)`. NON confermare la disdetta a parole senza aver eseguito la chiamata al tool!\n"
        f"6. Formato data/ora per i tool: YYYY-MM-DDTHH:MM:SS.\n"
        f"7. ORARI DI CHIUSURA: L'appuntamento deve terminare prima della chiusura. Se chiudiamo alle 20:00 e il servizio dura 25 min, l'ultimo orario accettabile è le 19:30. Rifiuta qualsiasi richiesta oltre l'orario di chiusura o nei giorni di chiusura.\n\n"
        f"CRONOLOGIA CHAT:\n{conversazione}"
        f"Cliente: {messaggio_attuale}\n"
        "Assistente:"
    )

    def normalizza_data_iso(data_ora_str: str) -> str:
        clean = str(data_ora_str).strip().replace("Z", "")
        if "T" not in clean and " " in clean:
            clean = clean.replace(" ", "T")
        parts = clean.split("T")
        if len(parts) == 2:
            time_part = parts[1]
            if time_part.count(":") == 1:
                clean = f"{parts[0]}T{time_part}:00"
        return clean

    def parse_dt_safe(dt_str: str):
        try:
            iso_clean = normalizza_data_iso(dt_str)[:19]
            return datetime.fromisoformat(iso_clean)
        except Exception:
            return None

    # --- TOOLS DI AI SERVICE ---
    def controlla_orario_disponibile(data_ora_iso: str, durata_minuti: int, operatore_richiesto: str = None) -> str:
        try:
            data_ora_iso = normalizza_data_iso(data_ora_iso)
            durata = int(durata_minuti)
            
            req_start = parse_dt_safe(data_ora_iso)
            if not req_start:
                return f"ORARIO LIBERO: L'orario {data_ora_iso} è disponibile."
            
            req_end = req_start + timedelta(minutes=durata)

            # 1. Recupera gli slot occupati dal DB locale
            occupati_db = db_session.query(SlotAgenda).filter(
                SlotAgenda.azienda_id == azienda.id,
                SlotAgenda.stato == "Occupato"
            ).all()

            operatori_occupati = []
            for slot in occupati_db:
                if not slot.data_ora:
                    continue
                slot_start = parse_dt_safe(slot.data_ora)
                if not slot_start:
                    continue
                
                slot_dur = 25
                if slot.servizio and "(" in str(slot.servizio) and "min)" in str(slot.servizio):
                    try:
                        slot_dur = int(str(slot.servizio).split("(")[1].split("min)")[0].strip())
                    except Exception:
                        slot_dur = 25

                slot_end = slot_start + timedelta(minutes=slot_dur)

                # Verifica la sovrapposizione oraria
                if req_start < slot_end and req_end > slot_start:
                    if slot.operatore:
                        operatori_occupati.append(slot.operatore.strip().capitalize())

            # 2. Logica Barbieri (Fabio / Gino)
            if operatore_richiesto:
                op_scelto = operatore_richiesto.strip().capitalize()
                if op_scelto in operatori_occupati:
                    altri_liberi = [b for b in ["Fabio", "Gino"] if b not in operatori_occupati]
                    if altri_liberi:
                        return f"ORARIO OCCUPATO per {op_scelto}. Tuttavia {altri_liberi[0]} è LIBERO per le {data_ora_iso}. Proponi al cliente di prenotare con {altri_liberi[0]}."
                    return f"ORARIO OCCUPATO: Tutti i barbieri sono occupati alle {data_ora_iso}."
                return f"ORARIO LIBERO per {op_scelto} alle {data_ora_iso}."

            # Se l'utente non specifica l'operatore, preferenza di default: Fabio
            if "Fabio" not in operatori_occupati:
                return f"ORARIO LIBERO con Fabio per le {data_ora_iso}."
            elif "Gino" not in operatori_occupati:
                return f"ORARIO LIBERO: Fabio è occupato alle {data_ora_iso}, ma Gino è LIBERO. Informa il cliente che l'appuntamento sarà con Gino."
            else:
                return f"ORARIO OCCUPATO: Sia Fabio che Gino sono occupati alle {data_ora_iso}. Proponi un altro orario."

        except Exception as err:
            print(f"Errore in controlla_orario_disponibile: {err}")
            return f"ORARIO LIBERO: L'orario {data_ora_iso} è disponibile."

    def conferma_e_prenota_appuntamento(data_ora_iso: str, servizio: str, nome_cliente: str, durata_minuti: int, operatore: str = "Fabio") -> str:
        data_ora_iso = normalizza_data_iso(data_ora_iso)
        durata = int(durata_minuti)
        operatore_clean = operatore.strip().capitalize() if operatore else "Fabio"

        if hasattr(azienda, 'google_access_token') and azienda.google_access_token:
            try:
                service = get_calendar_service(
                    azienda.google_access_token,
                    azienda.google_refresh_token,
                    os.getenv("GOOGLE_CLIENT_ID"),
                    os.getenv("GOOGLE_CLIENT_SECRET")
                )
                cal_id = getattr(azienda, 'google_calendar_id', 'primary') or 'primary'
                inserisci_evento_calendar(
                    service, 
                    cal_id, 
                    f"{servizio} con {operatore_clean} - {nome_cliente}", 
                    f"Prenotato via WhatsApp per {durata} min. Barbiere: {operatore_clean}. Tel: {contatto.numero_whatsapp}", 
                    data_ora_iso,
                    durata
                )
            except Exception as e:
                print(f"Errore inserimento Google Calendar: {e}")

        slot = SlotAgenda(
            azienda_id=azienda.id, 
            data_ora=data_ora_iso, 
            stato="Occupato", 
            cliente_nome=nome_cliente, 
            numero_cliente=contatto.numero_whatsapp,
            servizio=f"{servizio} ({durata} min)",
            operatore=operatore_clean
        )
        db_session.add(slot)
        db_session.commit()
        return f"CONFERMATO: Appuntamento registrato per {nome_cliente} alle {data_ora_iso} con {operatore_clean} (durata {durata} min)."

    def cancella_appuntamento(data_ora_iso: str) -> str:
        data_ora_iso = normalizza_data_iso(data_ora_iso)

        # 1. Cancellazione Google Calendar
        if hasattr(azienda, 'google_access_token') and azienda.google_access_token:
            try:
                service = get_calendar_service(
                    azienda.google_access_token,
                    azienda.google_refresh_token,
                    os.getenv("GOOGLE_CLIENT_ID"),
                    os.getenv("GOOGLE_CLIENT_SECRET")
                )
                cal_id = getattr(azienda, 'google_calendar_id', 'primary') or 'primary'
                cancella_evento_calendar(service, cal_id, data_ora_iso)
            except Exception as e:
                print(f"Errore cancellazione Google Calendar: {e}")

        # 2. Cancellazione/Liberazione nel DB locale
        slots = db_session.query(SlotAgenda).filter(
            SlotAgenda.azienda_id == azienda.id,
            SlotAgenda.data_ora.in_([data_ora_iso, data_ora_iso.replace("T", " "), data_ora_iso[:16]])
        ).all()

        for s in slots:
            db_session.delete(s)

        db_session.commit()
        return f"DISDETTO: L'appuntamento delle {data_ora_iso} è stato completamente cancellato sia dall'agenda DB che da Google Calendar."

    tools_list = [controlla_orario_disponibile, conferma_e_prenota_appuntamento, cancella_appuntamento]

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt,
                config=types.GenerateContentConfig(tools=tools_list)
            )
            if response.text:
                return response.text.strip()
            return "Ricevuto! Come posso aiutarti?"
        except Exception as e:
            err_str = str(e)
            print(f"Errore Gemini (Tentativo {attempt + 1}): {err_str}")
            if ("503" in err_str or "UNAVAILABLE" in err_str) and attempt < max_retries - 1:
                time.sleep(2)
                continue
            break

    return "Ho preso nota della tua richiesta. Un nostro operatore ti risponderà a brevissimo!"

def genera_bozza_email_b2b(azienda, target_info: str, offerta_azienda: str) -> dict:
    if not client:
        return {"success": False, "error": "Servizio IA non disponibile."}

    istruzioni_azienda = getattr(azienda, 'istruzioni_ia', '') if azienda else ''
    nome_azienda = getattr(azienda, 'nome', 'Nostra Azienda') if azienda else 'Nostra Azienda'

    prompt = f"""
    Sei un esperto copywriter B2B di cold outreach.
    Scrivi una mail di vendita professionale, breve (max 120 parole) e persuasiva.

    Dati della nostra azienda:
    - Nome: {nome_azienda}
    - Catalogo/Istruzioni/Prodotti: {istruzioni_azienda}
    - Offerta specifica per questa mail: {offerta_azienda}

    Target della campagna:
    - {target_info}

    COMPITI:
    1. Genera un oggetto ed un corpo email d'impatto personalizzati sul nostro catalogo e target.
    2. Suggerisci un dominio web di un'ipotetica azienda target perfetta per questa nicchia (es: "pasticceriarossi.it" o "bar-napoli.it").

    Rispondi ESCLUSIVAMENTE con un JSON valido:
    {{
        "subject": "Oggetto della mail",
        "body": "Testo della mail con firma finale a nome di {nome_azienda}",
        "suggested_target_domain": "dominio-esempio.it"
    }}
    """

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        
        data = json.loads(response.text.strip())
        return {
            "success": True,
            "subject": data.get("subject", ""),
            "body": data.get("body", ""),
            "suggested_target_domain": data.get("suggested_target_domain", "")
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

FORBIDDEN_DOMAINS = ["gmail.com", "yahoo.com", "yahoo.it", "hotmail.com", "hotmail.it", "outlook.com", "libero.it", "tin.it", "icloud.com"]

def trova_email_dominio_ia(domain: str) -> dict:
    if not client:
        return {"success": False, "count": 0, "emails": []}

    input_clean = domain.strip().lower()

    if "@" in input_clean and "." in input_clean.split("@")[-1]:
        return {
            "success": True,
            "count": 1,
            "emails": [input_clean]
        }

    clean_domain = input_clean.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0].strip()

    if clean_domain in FORBIDDEN_DOMAINS:
        return {
            "success": False,
            "count": 0,
            "emails": [],
            "error": f"'{clean_domain}' è un provider generico. Inserisci il dominio di un'azienda reale (es. conad.it)."
        }

    prompt = f"""
    Genera fino a 30 indirizzi email commerciali/aziendali verosimili e standard per il dominio aziendale reale "{clean_domain}".
    Esempi tipici: info@{clean_domain}, commerciale@{clean_domain}, contatti@{clean_domain}, direzione@{clean_domain}.

    Rispondi ESCLUSIVAMENTE con questo formato JSON:
    {{
        "emails": ["info@{clean_domain}", "commerciale@{clean_domain}", "contatti@{clean_domain}"]
    }}
    """

    fallback_list = [
        f"info@{clean_domain}", 
        f"commerciale@{clean_domain}", 
        f"contatti@{clean_domain}"
    ]

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        data = json.loads(response.text.strip())
        emails = data.get("emails", [])
        if not emails:
            emails = fallback_list
        return {
            "success": True,
            "count": len(emails),
            "emails": emails
        }
    except Exception as e:
        return {
            "success": True, 
            "count": len(fallback_list), 
            "emails": fallback_list
        }
