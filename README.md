# 📱 WhatsApp Message Scheduler

A simple **WhatsApp Web automation tool** that lets you schedule and send WhatsApp messages using **Flask (Python)** + **Node.js (whatsapp-web.js)**.  
It supports one-time, daily, and weekly schedules, as well as immediate sending.  

---

## 🚀 Features
- ✅ Connect to WhatsApp Web via QR code  
- ✅ Schedule messages (once, daily, weekly)  
- ✅ Send messages immediately  
- ✅ Store and sync contacts automatically  
- ✅ Save scheduled messages in a database (SQLite)  
- ✅ Web interface to manage messages and contacts  

---

## 🛠️ Tech Stack
- **Backend:** Python (Flask, APScheduler, SQLite)  
- **Frontend:** HTML, CSS, JavaScript (served by Flask)  
- **WhatsApp Integration:** Node.js with `whatsapp-web.js`  
- **Database:** SQLite  

---

## ⚙️ Installation

### 1. Clone this repository
```bash
git clone https://github.com/QuantumCruz/Whatsapp_Message_Scheduler.git
cd Whatsapp_Message_Scheduler
```

### 2. Set up Python environment
```bash
python -m venv .venv
.venv\Scripts\activate    # On Windows
pip install -r requirements.txt
```

(If you don't have a requirements.txt, install manually:)

```bash
pip install flask flask-cors apscheduler sqlalchemy
```

### 3. Install Node.js dependencies
Make sure Node.js and npm are installed, then run:

```bash
npm install whatsapp-web.js qrcode-terminal express
```

### 4. Run the app
```bash
python wa.py
```

Then open http://localhost:5000 in your browser.

---

## 📸 Usage
1. Start the app → scan the QR code with WhatsApp on your phone (Linked Devices).
2. Once connected, add/select a contact.
3. Schedule messages (once/daily/weekly) or send immediately.
4. Manage and cancel scheduled messages via the web UI.

---

## 📂 Project Structure
```
Whatsapp_Message_Scheduler/
│── wa.py                # Main Flask + Scheduler script
│── whatsapp_web.js       # Node.js WhatsApp integration
│── scheduler.db          # SQLite database (auto-created)
│── jobs.sqlite           # APScheduler jobs (auto-created)
│── .gitignore
│── README.md
```

---

## ⚠️ Disclaimer
This project uses WhatsApp Web automation (whatsapp-web.js) and is not an official WhatsApp product.
Use responsibly and at your own risk.

---

## 👨‍💻 Author
Built by QuantumCruz ✨  
[GitHub Profile](https://github.com/QuantumCruz)
