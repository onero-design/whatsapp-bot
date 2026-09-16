import os
import json
import time
from datetime import datetime, timedelta
from google import genai
from google.genai import types
from sqlalchemy.orm import Session

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

from calendar_service import get_calendar_service, verifica_disponibilita_calendar, inserisci_evento_calendar

def genera_risposta_gemini(azienda, contatto, messaggio_attuale: str, db_session: Session, SlotAgenda, Messaggio) -> str:
    if not client:
        return "Servizio IA temporaneamente non disponibile."

    # 1. Recupero dello storico messaggi
    storico = db_session.query(Messaggio).filter(
        Messaggio.contatto_id == contatto.id
    ).order_by(Messaggio.inviato_il.desc()).limit(6).all()
    storico.reverse()

    conversazione = ""
    for msg in storico:
        ruolo = "Cliente" if msg.direzione == "INBOUND" else "Assistente"
        conversazione += f"{ruolo}: {msg.testo}\n"

    ora_attuale = datetime.now()

    prompt = (
        f"Data e Ora attuale del sistema: {ora_attuale.strftime('%d/%m/%Y alle %H:%M')} (Anno: {ora_attuale.year}).\n"
        f"Sei l'assistente virtuale di {azienda.nome}.\n"
        f"ISTRUZIONI AZIENDALI:\n{azienda.istruzioni_ia}\n\n"
        f"REGOLE FONDAMENTALI PRENOTAZIONE:\n"
        f"1. PRIMA di confermare o registrare qualsiasi appuntamento, DEVI TASSATIVAMENTE chiamare la funzione `controlla_orario_disponibile(data_ora_iso)`.\n"
        f"2. Se `controlla_orario_disponibile` risponde che l'orario è OCCUPATO, NON PRENOTARE! Riferisci al cliente che l'orario non è disponibile e chiedigli di scegliere un altro orario.\n"
        f"3. Solo se l'orario risulta LIBERO, chiama `conferma_e_prenota_appuntamento` per registrarlo.\n"
        f"4. Il formato della data e ora per i tool deve essere ISO standard: YYYY-MM-DDTHH:MM:SS (es. 2026-09-17T18:00:00).\n\n"
        f"CRONOLOGIA CHAT:\n{conversazione}"
        f"Cliente: {messaggio_attuale}\n"
        "Assistente:"
    )

    # --- HELPER PARSING FORMATI DATE ISO / SPAZIO ---
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

    # --- TOOLS DI VERIFICA E PRENOTAZIONE CALENDAR ---
    def controlla_orario_disponibile(data_ora_iso: str) -> str:
        """Verifica se uno slot è libero sul Google Calendar e nel DB locale. Formato data_ora_iso: YYYY-MM-DDTHH:MM:SS."""
        data_ora_iso = normalizza_data_iso(data_ora_iso)
        
        # 1. Controllo primario su DB locale
        slot_occupato = db_session.query(SlotAgenda).filter(
            SlotAgenda.azienda_id == azienda.id,
            SlotAgenda.stato == "Occupato",
            SlotAgenda.data_ora.in_([data_ora_iso, data_ora_iso.replace("T", " "), data_ora_iso[:16]])
        ).first()

        if slot_occupato:
            return f"ORARIO OCCUPATO: L'orario {data_ora_iso} è già stato prenotato da un altro cliente nel sistema. Scegli o proponi un orario diverso."

        # 2. Controllo su Google Calendar se collegato
        if hasattr(azienda, 'google_access_token') and azienda.google_access_token:
            try:
                service = get_calendar_service(
                    azienda.google_access_token,
                    azienda.google_refresh_token,
                    os.getenv("GOOGLE_CLIENT_ID"),
                    os.getenv("GOOGLE_CLIENT_SECRET")
                )
                cal_id = getattr(azienda, 'google_calendar_id', 'primary') or 'primary'
                is_free = verifica_disponibilita_calendar(service, cal_id, data_ora_iso)
                if not is_free:
                    return f"ORARIO OCCUPATO: L'orario {data_ora_iso} è occupato su Google Calendar. Proponi un altro orario."
            except Exception as e:
                print(f"Errore verifica Google Calendar: {e}")

        return f"ORARIO LIBERO: L'orario {data_ora_iso} è completamente disponibile. Puoi procedere alla prenotazione."

    def conferma_e_prenota_appuntamento(data_ora_iso: str, servizio: str, nome_cliente: str) -> str:
        """Prenota l'appuntamento sia su Google Calendar che sul DB locale."""
        data_ora_iso = normalizza_data_iso(data_ora_iso)

        # 1. Scrittura su Google Calendar
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
                    f"{servizio} - {nome_cliente}", 
                    f"Prenotato via WhatsApp: {contatto.numero_whatsapp}", 
                    data_ora_iso
                )
            except Exception as e:
                print(f"Errore inserimento Google Calendar: {e}")

        # 2. Salvataggio su DB locale per memoria interna
        slot = db_session.query(SlotAgenda).filter(
            SlotAgenda.azienda_id == azienda.id,
            SlotAgenda.data_ora.in_([data_ora_iso, data_ora_iso.replace("T", " "), data_ora_iso[:16]])
        ).first()
        
        if not slot:
            slot = SlotAgenda(
                azienda_id=azienda.id, 
                data_ora=data_ora_iso, 
                stato="Occupato", 
                cliente_nome=nome_cliente, 
                numero_cliente=contatto.numero_whatsapp,
                servizio=servizio
            )
            db_session.add(slot)
        else:
            slot.stato = "Occupato"
            slot.cliente_nome = nome_cliente
            slot.numero_cliente = contatto.numero_whatsapp
            slot.servizio = servizio
            
        db_session.commit()
        return f"CONFERMATO: Appuntamento registrato per {nome_cliente} in data {data_ora_iso} per {servizio}."

    tools_list = [controlla_orario_disponibile, conferma_e_prenota_appuntamento]

    # 3. Chiamata a Gemini 3.6 Flash
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

# --- GENERATORE DI BOZZE EMAIL B2B ---
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

# --- RICERCA EMAIL DOMINIO ---
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
