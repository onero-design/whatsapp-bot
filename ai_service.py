import os
import json
from openai import OpenAI

def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAI(api_key=api_key)

def risposta_ia_whatsapp(messaggio_utente: str, istruzioni_azienda: str, slot_disponibili: list = None) -> str:
    """Gestisce le risposte automatiche dell'IA per i messaggi WhatsApp del cliente."""
    client = get_openai_client()
    if not client:
        return "Servizio IA temporaneamente non disponibile (API Key non configurata)."

    prompt = f"""
    Sei l'assistente virtuale dell'azienda. Rispondi al cliente in modo cortese, chiaro e conciso.
    
    ISTRUZIONI AZIENDALI:
    {istruzioni_azienda}
    
    SLOT AGENDA DISPONIBILI:
    {slot_disponibili if slot_disponibili else 'Nessuno slot specificato.'}
    
    MESSAGGIO RICEVUTO DAL CLIENTE:
    "{messaggio_utente}"
    
    Rispondi direttamente al cliente in italiano in stile conversazionale da chat WhatsApp.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Errore IA WhatsApp: {e}")
        return "Grazie per averci contattato! Un nostro operatore ti risponderà il prima possibile."

def genera_bozza_email_b2b(target_info: str, offerta_azienda: str) -> dict:
    """Genera la bozza email marketing B2B ed estrae il dominio ipotizzato."""
    client = get_openai_client()
    if not client:
        return {
            "success": False, 
            "error": "OPENAI_API_KEY non presente nelle variabili d'ambiente.",
            "subject": f"Proposta per {target_info}",
            "body": f"Gentile team di {target_info},\n\nVorremmo proporvi la nostra offerta: {offerta_azienda}.\n\nRestiamo a disposizione.",
            "domain": ""
        }

    prompt = f"""
    Sei un esperto di email marketing B2B.
    
    Obiettivi:
    1. Genera una bozza di email persuasiva, professionale e breve per il seguente target: "{target_info}".
       L'offerta da proporre è: "{offerta_azienda}".
    2. Identifica o ipotizza il dominio web aziendale principale più probabile per "{target_info}" (es. se target è "Conad", il dominio è "conad.it"; se è "Bar Sport", ipotizza "barsport.it").

    Rispondi ESCLUSIVAMENTE con un oggetto JSON valido (senza formattazione markdown tipo ```json):
    {{
        "subject": "Oggetto dell'email qui",
        "body": "Testo dell'email qui",
        "suggested_domain": "dominioipotizzato.it"
    }}
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        content = response.choices[0].message.content.strip()
        
        if content.startswith("```json"):
            content = content.replace("```json", "").replace("```", "").strip()
        elif content.startswith("```"):
            content = content.replace("```", "").strip()
            
        data = json.loads(content)
        return {
            "success": True,
            "subject": data.get("subject", ""),
            "body": data.get("body", ""),
            "domain": data.get("suggested_domain", "").lower()
        }
    except Exception as e:
        print(f"Errore generazione IA Email: {e}")
        return {
            "success": False, 
            "error": str(e),
            "subject": f"Proposta per {target_info}",
            "body": f"Gentile team di {target_info},\n\nVorremmo proporvi la nostra offerta: {offerta_azienda}.\n\nRestiamo a disposizione.",
            "domain": ""
        }
