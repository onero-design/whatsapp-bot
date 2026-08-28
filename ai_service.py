import os
import json
from datetime import datetime
from google import genai
from google.genai import types
from sqlalchemy.orm import Session

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# --- FUNZIONI STRUMENTI (TOOLS) ---
def cerca_slot_disponibile(azienda_id: int, data_ora: str, db: Session, SlotAgenda) -> str:
    slot = db.query(SlotAgenda).filter(
        SlotAgenda.azienda_id == azienda_id,
        SlotAgenda.data_ora == data_ora
    ).first()
    
    if not slot or slot.stato == "Disponibile":
        return f"Lo slot per il {data_ora} è DISPONIBILE."
    return f"Lo slot per il {data_ora} è già OCCUPATO."

def fissa_appuntamento(azienda_id: int, data_ora: str, servizio: str, nome_cliente: str, numero_cliente: str, db: Session, SlotAgenda) -> str:
    slot = db.query(SlotAgenda).filter(
        SlotAgenda.azienda_id == azienda_id,
        SlotAgenda.data_ora == data_ora
    ).first()
    
    if not slot:
        slot = SlotAgenda(
            azienda_id=azienda_id, 
            data_ora=data_ora, 
            stato="Occupato", 
            cliente_nome=nome_cliente, 
            numero_cliente=numero_cliente,
            servizio=servizio
        )
        db.add(slot)
    else:
        slot.stato = "Occupato"
        slot.cliente_nome = nome_cliente
        slot.numero_cliente = numero_cliente
        slot.servizio = servizio
        
    db.commit()
    return f"Appuntamento confermato con successo per {nome_cliente} in data {data_ora} per il servizio {servizio}."

# --- MOTORE DI RISPOSTA IA (WHATSAPP) ---
def genera_risposta_gemini(azienda, contatto, messaggio_attuale: str, db_session: Session, SlotAgenda, Messaggio) -> str:
    if not client:
        return "Servizio IA non disponibile."

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
        f"Data e Ora attuale: {ora_attuale.strftime('%d/%m/%Y alle %H:%M')}.\n"
        f"Sei l'assistente virtuale di {azienda.nome}.\n"
        f"ISTRUZIONI AZIENDALI:\n{azienda.istruzioni_ia}\n\n"
        f"CRONOLOGIA CHAT:\n{conversazione}"
        f"Cliente: {messaggio_attuale}\n"
        "Assistente:"
    )

    def verifica_disponibilita(data_ora: str) -> str:
        """Verifica se uno slot è disponibile."""
        return cerca_slot_disponibile(azienda.id, data_ora, db_session, SlotAgenda)

    def prenota_appuntamento(data_ora: str, servizio: str, nome_cliente: str) -> str:
        """Prenota un appuntamento salvando data_ora, servizio e nome cliente."""
        return fissa_appuntamento(azienda.id, data_ora, servizio, nome_cliente, contatto.numero_whatsapp, db_session, SlotAgenda)

    tools_list = [verifica_disponibilita, prenota_appuntamento]

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
            config=types.GenerateContentConfig(tools=tools_list)
        )
        return response.text.strip()
    except Exception as e:
        print(f"Errore Gemini WhatsApp: {e}")
        return "Grazie per il messaggio! Un operatore ti risponderà a breve."

# --- GENERATORE DI BOZZE EMAIL B2B ---
def genera_bozza_email_b2b(azienda, target_info: str, offerta_azienda: str) -> dict:
    if not client:
        return {"success": False, "error": "Servizio IA non disponibile."}

    # Estraiamo le istruzioni/catalogo salvate per l'azienda
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

    # Se l'utente inserisce direttamente un'email valida completa
    if "@" in input_clean and "." in input_clean.split("@")[-1]:
        return {
            "success": True,
            "count": 1,
            "emails": [input_clean]
        }

    clean_domain = input_clean.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0].strip()

    # Blocco domini gratuiti/generici: un venditore non deve contattare info@gmail.com
    if clean_domain in FORBIDDEN_DOMAINS:
        return {
            "success": False,
            "count": 0,
            "emails": [],
            "error": f"'{clean_domain}' è un provider generico. Inserisci il dominio di un'azienda reale (es. conad.it)."
        }

    prompt = f"""
    Genera fino a 5 indirizzi email commerciali/aziendali verosimili e standard per il dominio aziendale reale "{clean_domain}".
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
