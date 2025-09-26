import os
import json
import logging
import sqlite3
import time
import subprocess
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from pathlib import Path
import threading
import signal

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("whatsapp_scheduler.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler_instance = None

# Wrapper for APScheduler jobs (must be top-level)
def send_scheduled_message_wrapper(message_id):
    global scheduler_instance
    if scheduler_instance:
        scheduler_instance.send_scheduled_message(message_id)


@dataclass
class ScheduledMessage:
    id: str
    contact_name: str
    phone_number: str
    message: str
    schedule_type: str
    datetime: str
    file_path: Optional[str] = None
    status: str = "scheduled"
    created_at: str = None


class WhatsAppWebAPI:
    """
    WhatsApp Web API using Node.js whatsapp-web.js library
    """

    def __init__(self):
        self.node_process = None
        self.is_connected = False
        self.qr_code = None
        self.node_ready = False
        self.setup_complete = False
        
        # Start setup in background
        setup_thread = threading.Thread(target=self.setup_node_environment, daemon=True)
        setup_thread.start()

    def setup_node_environment(self):
        """Setup Node.js environment and install dependencies"""
        try:
            # Check if Node.js is available
            result = subprocess.run(["node", "--version"], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                logger.error("Node.js not found. Please install Node.js from: https://nodejs.org/")
                return False

            logger.info(f"Node.js version: {result.stdout.strip()}")

            # Create package.json
            package_json = {
                "name": "whatsapp-scheduler",
                "version": "1.0.0",
                "description": "Free WhatsApp scheduler",
                "dependencies": {
                    "whatsapp-web.js": "^1.23.0",
                    "qrcode-terminal": "^0.12.0",
                    "express": "^4.18.2"
                }
            }

            with open("package.json", "w") as f:
                json.dump(package_json, f, indent=2)

            # Install dependencies
            if not os.path.exists("node_modules"):
                logger.info("Installing Node.js dependencies... This may take a few minutes.")
                result = subprocess.run(["npm", "install"], 
                                      capture_output=True, text=True, timeout=300)
                if result.returncode != 0:
                    logger.error(f"Failed to install dependencies: {result.stderr}")
                    return False
                logger.info("Dependencies installed successfully")

            # Create the WhatsApp script
            self.create_whatsapp_script()
            
            # Start WhatsApp session
            time.sleep(2)  # Brief delay before starting
            self.start_whatsapp_session()
            
            self.setup_complete = True
            return True

        except subprocess.TimeoutExpired:
            logger.error("Timeout during Node.js setup")
            return False
        except Exception as e:
            logger.error(f"Setup error: {e}")
            return False

    def create_whatsapp_script(self):
        """Create the Node.js WhatsApp Web script"""
        script_content = '''
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const express = require('express');
const fs = require('fs');

const app = express();
app.use(express.json());

// Initialize WhatsApp client
const client = new Client({
    authStrategy: new LocalAuth({
        dataPath: './whatsapp_session'
    }),
    puppeteer: {
        headless: true,
        args: [
            '--no-sandbox', 
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--no-first-run',
            '--no-zygote',
            '--disable-gpu'
        ]
    }
});

let isClientReady = false;
let currentQR = null;
let clientState = 'INITIALIZING';

console.log('Starting WhatsApp Web client...');

// Generate QR code for authentication
client.on('qr', (qr) => {
    console.log('QR Code received, scan with your phone');
    currentQR = qr;
    clientState = 'QR_RECEIVED';
    qrcode.generate(qr, {small: true});
    
    // Save QR to file
    fs.writeFileSync('./qr_code.txt', qr);
});

// Client is ready
client.on('ready', () => {
    console.log('WhatsApp Client is ready!');
    isClientReady = true;
    clientState = 'READY';
    currentQR = null;
    
    // Remove QR code file when ready
    if (fs.existsSync('./qr_code.txt')) {
        fs.unlinkSync('./qr_code.txt');
    }
});

// Client authenticated
client.on('authenticated', () => {
    console.log('WhatsApp Client authenticated!');
    clientState = 'AUTHENTICATED';
});

// Authentication failure
client.on('auth_failure', (msg) => {
    console.error('Authentication failed:', msg);
    clientState = 'AUTH_FAILED';
});

// Client disconnected
client.on('disconnected', (reason) => {
    console.log('WhatsApp Client disconnected:', reason);
    isClientReady = false;
    clientState = 'DISCONNECTED';
});

// Initialize client
client.initialize().catch(err => {
    console.error('Failed to initialize client:', err);
    clientState = 'INITIALIZATION_FAILED';
});

// API Routes
app.get('/status', (req, res) => {
    let qr = null;
    if (fs.existsSync('./qr_code.txt')) {
        try {
            qr = fs.readFileSync('./qr_code.txt', 'utf8');
        } catch (e) {
            console.error('Failed to read QR file:', e);
        }
    }
    
    res.json({ 
        ready: isClientReady,
        qr: qr,
        state: clientState
    });
});

app.post('/send', async (req, res) => {
    if (!isClientReady) {
        return res.json({ success: false, error: 'WhatsApp client not ready' });
    }

    try {
        const { phone, message, filePath } = req.body;
        
        // Format phone number
        let chatId = phone.replace(/[^0-9]/g, '');
        if (!chatId.includes('@')) {
            chatId = chatId + '@c.us';
        }
        
        if (filePath && fs.existsSync(filePath)) {
            // Send media message
            const media = MessageMedia.fromFilePath(filePath);
            await client.sendMessage(chatId, media, { caption: message });
        } else {
            // Send text message
            await client.sendMessage(chatId, message);
        }

        console.log(`Message sent to ${chatId}: ${message}`);
        res.json({ success: true });
    } catch (error) {
        console.error('Send message error:', error);
        res.json({ success: false, error: error.message });
    }
});

app.get('/contacts', async (req, res) => {
    if (!isClientReady) {
        return res.json({ success: false, error: 'WhatsApp client not ready' });
    }

    try {
        const contacts = await client.getContacts();
        const formattedContacts = contacts
            .filter(contact => contact.name && contact.id.user && !contact.id.server.includes('broadcast'))
            .slice(0, 100) // Limit to 100 contacts
            .map(contact => ({
                name: contact.name,
                phone: '+' + contact.id.user
            }));

        res.json({ success: true, contacts: formattedContacts });
    } catch (error) {
        console.error('Get contacts error:', error);
        res.json({ success: false, error: error.message });
    }
});

// Health check
app.get('/health', (req, res) => {
    res.json({ status: 'ok', ready: isClientReady, state: clientState });
});

// Start Express server
const PORT = process.env.NODE_PORT || 3001;
const server = app.listen(PORT, 'localhost', () => {
    console.log(`WhatsApp API server running on http://localhost:${PORT}`);
});

// Graceful shutdown
const shutdown = async () => {
    console.log('Shutting down WhatsApp client...');
    server.close();
    if (client) {
        await client.destroy();
    }
    process.exit(0);
};

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
process.on('uncaughtException', (error) => {
    console.error('Uncaught exception:', error);
});
        '''

        with open("whatsapp_web.js", "w") as f:
            f.write(script_content)
        logger.info("Created WhatsApp Web script")

    def start_whatsapp_session(self):
        """Start the WhatsApp Web session"""
        try:
            # Start Node.js process
            self.node_process = subprocess.Popen(
                ["node", "whatsapp_web.js"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                cwd=os.getcwd()
            )
            
            logger.info("Started Node.js WhatsApp process")
            
            # Monitor process output
            def monitor_output():
                try:
                    for line in iter(self.node_process.stdout.readline, ''):
                        if line.strip():
                            logger.info(f"WhatsApp: {line.strip()}")
                            if "WhatsApp Client is ready!" in line:
                                self.is_connected = True
                                self.node_ready = True
                            elif "QR Code received" in line:
                                self.qr_code = self.get_qr_code()
                except Exception as e:
                    logger.error(f"Error monitoring Node.js output: {e}")
            
            # Start monitoring in separate thread
            monitor_thread = threading.Thread(target=monitor_output, daemon=True)
            monitor_thread.start()
            
            # Wait for server to be ready
            self.wait_for_server()
            
        except Exception as e:
            logger.error(f"Failed to start WhatsApp session: {e}")

    def wait_for_server(self):
        """Wait for Node.js server to be ready"""
        import requests
        
        for i in range(30):  # Wait up to 30 seconds
            try:
                response = requests.get("http://localhost:3001/health", timeout=2)
                if response.status_code == 200:
                    logger.info("Node.js server is ready")
                    self.node_ready = True
                    return True
            except:
                pass
            time.sleep(1)
        
        logger.warning("Node.js server not ready after 30 seconds")
        return False

    def get_qr_code(self):
        """Get QR code for authentication"""
        try:
            if os.path.exists("qr_code.txt"):
                with open("qr_code.txt", "r") as f:
                    return f.read().strip()
        except Exception as e:
            logger.error(f"Failed to read QR code: {e}")
        return None

    def send_message(self, phone_number: str, message: str, file_path: str = None) -> bool:
        """Send message via WhatsApp Web"""
        if not self.node_ready:
            logger.error("Node.js server not ready")
            return False
            
        try:
            import requests
            
            response = requests.post(
                "http://localhost:3001/send",
                json={
                    "phone": phone_number,
                    "message": message,
                    "filePath": file_path
                },
                timeout=30
            )
            
            result = response.json()
            if result.get("success"):
                logger.info(f"Message sent successfully to {phone_number}")
                return True
            else:
                logger.error(f"Failed to send message: {result.get('error')}")
                return False

        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False

    def get_whatsapp_contacts(self):
        """Get contacts from WhatsApp"""
        if not self.node_ready:
            return {}
            
        try:
            import requests
            
            response = requests.get("http://localhost:3001/contacts", timeout=30)
            result = response.json()
            
            if result.get("success"):
                return {contact["name"]: contact["phone"] for contact in result["contacts"]}
            else:
                logger.error(f"Failed to get contacts: {result.get('error')}")
                return {}

        except Exception as e:
            logger.error(f"Failed to get WhatsApp contacts: {e}")
            return {}

    def get_status(self):
        """Get WhatsApp client status"""
        if not self.node_ready:
            return {"ready": False, "qr": None, "error": "Node.js server not ready"}
            
        try:
            import requests
            
            response = requests.get("http://localhost:3001/status", timeout=5)
            return response.json()
            
        except Exception as e:
            logger.error(f"Failed to get status: {e}")
            return {"ready": False, "qr": None, "error": str(e)}

    def shutdown(self):
        """Shutdown WhatsApp session"""
        if self.node_process:
            try:
                self.node_process.terminate()
                self.node_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.node_process.kill()
            self.node_process = None
        
        # Clean up files
        for file in ["qr_code.txt", "package.json"]:
            if os.path.exists(file):
                try:
                    os.remove(file)
                except:
                    pass


class MessageDatabase:
    """Database handler for storing scheduled messages"""

    def __init__(self, db_path: str = "scheduler.db"):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Initialize database tables"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_messages (
                    id TEXT PRIMARY KEY,
                    contact_name TEXT NOT NULL,
                    phone_number TEXT NOT NULL,
                    message TEXT NOT NULL,
                    schedule_type TEXT NOT NULL,
                    datetime TEXT NOT NULL,
                    file_path TEXT,
                    status TEXT DEFAULT 'scheduled',
                    created_at TEXT NOT NULL
                )
            """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS contacts (
                    name TEXT PRIMARY KEY,
                    phone_number TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
            conn.commit()

    def add_message(self, message: ScheduledMessage) -> bool:
        """Add scheduled message to database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO scheduled_messages 
                    (id, contact_name, phone_number, message, schedule_type, datetime, file_path, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        message.id,
                        message.contact_name,
                        message.phone_number,
                        message.message,
                        message.schedule_type,
                        message.datetime,
                        message.file_path,
                        message.status,
                        message.created_at,
                    ),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to add message to database: {e}")
            return False

    def get_messages(self, status: str = None) -> List[ScheduledMessage]:
        """Get scheduled messages from database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute(
                    "SELECT * FROM scheduled_messages WHERE status = ?", (status,)
                )
            else:
                cursor.execute("SELECT * FROM scheduled_messages ORDER BY datetime")

            rows = cursor.fetchall()
            return [ScheduledMessage(*row) for row in rows]

    def update_message_status(self, message_id: str, status: str) -> bool:
        """Update message status"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE scheduled_messages SET status = ? WHERE id = ?",
                    (status, message_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to update message status: {e}")
            return False

    def delete_message(self, message_id: str) -> bool:
        """Delete message from database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM scheduled_messages WHERE id = ?", (message_id,)
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to delete message: {e}")
            return False

    def add_contact(self, name: str, phone_number: str) -> bool:
        """Add contact to database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO contacts (name, phone_number) VALUES (?, ?)",
                    (name, phone_number),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to add contact: {e}")
            return False

    def get_contacts(self) -> Dict[str, str]:
        """Get all contacts from database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name, phone_number FROM contacts")
            return dict(cursor.fetchall())


class WhatsAppScheduler:
    """Main scheduler class using WhatsApp Web API"""

    def __init__(self):
        self.db = MessageDatabase()
        self.whatsapp_api = WhatsAppWebAPI()
        self.scheduler = BackgroundScheduler(
            jobstores={"default": SQLAlchemyJobStore(url="sqlite:///jobs.sqlite")}
        )
        self.scheduler.start()
        self.load_existing_jobs()
        
        # Schedule contact sync
        self.schedule_contact_sync()

    def schedule_contact_sync(self):
        """Schedule periodic contact syncing"""
        def sync_contacts():
            time.sleep(30)  # Wait for WhatsApp to be ready
            while True:
                try:
                    if self.whatsapp_api.is_connected:
                        contacts = self.whatsapp_api.get_whatsapp_contacts()
                        for name, phone in contacts.items():
                            self.db.add_contact(name, phone)
                        if contacts:
                            logger.info(f"Synced {len(contacts)} contacts from WhatsApp")
                except Exception as e:
                    logger.error(f"Contact sync error: {e}")
                
                time.sleep(300)  # Sync every 5 minutes

        sync_thread = threading.Thread(target=sync_contacts, daemon=True)
        sync_thread.start()

    def load_existing_jobs(self):
        """Load existing scheduled jobs from database"""
        scheduled_messages = self.db.get_messages("scheduled")
        for message in scheduled_messages:
            try:
                schedule_time = datetime.fromisoformat(message.datetime)
                if schedule_time > datetime.now():
                    self.scheduler.add_job(
                        func=send_scheduled_message_wrapper,
                        trigger="date",
                        run_date=schedule_time,
                        args=[message.id],
                        id=message.id,
                        replace_existing=True,
                    )
            except Exception as e:
                logger.error(f"Failed to load job {message.id}: {e}")

    def schedule_message(
        self,
        contact_name: str,
        phone_number: str,
        message: str,
        schedule_time: datetime,
        schedule_type: str = "once",
        file_path: str = None,
    ) -> str:
        """Schedule a new message"""
        message_id = f"msg_{int(datetime.now().timestamp() * 1000)}"

        scheduled_msg = ScheduledMessage(
            id=message_id,
            contact_name=contact_name,
            phone_number=phone_number,
            message=message,
            schedule_type=schedule_type,
            datetime=schedule_time.isoformat(),
            file_path=file_path,
            created_at=datetime.now().isoformat(),
        )

        if self.db.add_message(scheduled_msg):
            try:
                if schedule_type == "once":
                    self.scheduler.add_job(
                        func=send_scheduled_message_wrapper,
                        trigger="date",
                        run_date=schedule_time,
                        args=[message_id],
                        id=message_id,
                    )
                elif schedule_type == "daily":
                    self.scheduler.add_job(
                        func=send_scheduled_message_wrapper,
                        trigger="cron",
                        hour=schedule_time.hour,
                        minute=schedule_time.minute,
                        args=[message_id],
                        id=message_id,
                    )
                elif schedule_type == "weekly":
                    self.scheduler.add_job(
                        func=send_scheduled_message_wrapper,
                        trigger="cron",
                        day_of_week=schedule_time.weekday(),
                        hour=schedule_time.hour,
                        minute=schedule_time.minute,
                        args=[message_id],
                        id=message_id,
                    )

                logger.info(f"Message {message_id} scheduled successfully")
                return message_id

            except Exception as e:
                logger.error(f"Failed to schedule job: {e}")
                self.db.delete_message(message_id)
                return None

        return None

    def send_scheduled_message(self, message_id: str):
        """Send a scheduled message"""
        messages = [msg for msg in self.db.get_messages() if msg.id == message_id]
        if not messages:
            logger.error(f"Message {message_id} not found")
            return

        message = messages[0]

        try:
            success = self.whatsapp_api.send_message(
                message.phone_number, message.message, message.file_path
            )

            if success:
                self.db.update_message_status(message_id, "sent")
                logger.info(f"Message {message_id} sent successfully")

                # Remove one-time jobs after sending
                if message.schedule_type == "once":
                    try:
                        self.scheduler.remove_job(message_id)
                    except:
                        pass
            else:
                self.db.update_message_status(message_id, "failed")
                logger.error(f"Failed to send message {message_id}")

        except Exception as e:
            logger.error(f"Error sending message {message_id}: {e}")
            self.db.update_message_status(message_id, "failed")

    def send_immediately(
        self, phone_number: str, message: str, file_path: str = None
    ) -> bool:
        """Send message immediately"""
        return self.whatsapp_api.send_message(phone_number, message, file_path)

    def cancel_scheduled_message(self, message_id: str) -> bool:
        """Cancel a scheduled message"""
        try:
            self.scheduler.remove_job(message_id)
            self.db.delete_message(message_id)
            logger.info(f"Message {message_id} cancelled successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel message {message_id}: {e}")
            return False

    def get_scheduled_messages(self) -> List[ScheduledMessage]:
        """Get all scheduled messages"""
        return self.db.get_messages()

    def get_whatsapp_status(self):
        """Get WhatsApp connection status"""
        return self.whatsapp_api.get_status()

    def shutdown(self):
        """Shutdown the scheduler"""
        self.scheduler.shutdown()
        self.whatsapp_api.shutdown()


# Flask Web Interface
app = Flask(__name__)
CORS(app)
scheduler_instance = None


@app.route("/")
def index():
    """Serve the web interface"""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WhatsApp Scheduler</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #25D366 0%, #128C7E 100%);
            min-height: 100vh; padding: 20px;
        }
        .container { 
            max-width: 1200px; margin: 0 auto; background: white;
            border-radius: 20px; box-shadow: 0 20px 40px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        .header { 
            background: linear-gradient(45deg, #25D366, #128C7E);
            color: white; padding: 30px; text-align: center;
        }
        .header h1 { font-size: 2.5rem; margin-bottom: 10px; font-weight: 300; }
        
        .status-section { padding: 20px; background: #f8f9ff; border-bottom: 1px solid #e1e8ed; }
        .status-indicator { 
            padding: 15px; border-radius: 10px; margin-bottom: 15px;
            font-weight: 500; text-align: center;
        }
        .status-indicator.connected { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .status-indicator.connecting { background: #fff3cd; color: #856404; border: 1px solid #ffeaa7; }
        .status-indicator.disconnected { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        
        .qr-section { text-align: center; padding: 20px; }
        .qr-code { 
            max-width: 300px; margin: 20px auto; padding: 20px;
            background: white; border-radius: 10px; box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .main-content { display: grid; grid-template-columns: 1fr 1fr; gap: 40px; padding: 40px; }
        .form-section { 
            background: #f8f9ff; padding: 30px; border-radius: 15px; border: 1px solid #e1e8ed;
        }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; font-weight: 600; color: #555; }
        input, select, textarea { 
            width: 100%; padding: 15px; border: 2px solid #e1e8ed;
            border-radius: 10px; font-size: 1rem; transition: all 0.3s ease;
        }
        input:focus, select:focus, textarea:focus { 
            outline: none; border-color: #25D366;
            box-shadow: 0 0 0 3px rgba(37, 211, 102, 0.1);
        }
        textarea { resize: vertical; min-height: 100px; }
        
        .btn { 
            background: linear-gradient(45deg, #25D366, #128C7E);
            color: white; border: none; padding: 15px 30px; border-radius: 10px;
            font-size: 1rem; font-weight: 600; cursor: pointer;
            transition: all 0.3s ease; width: 100%;
        }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 10px 20px rgba(37, 211, 102, 0.3); }
        .btn:disabled { background: #ccc; cursor: not-allowed; transform: none; }
        .btn-secondary { background: linear-gradient(45deg, #667eea, #764ba2); }
        
        .status { padding: 15px; border-radius: 10px; margin-bottom: 20px; font-weight: 500; }
        .status.success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .status.error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        
        .message-card { 
            background: linear-gradient(135deg, #f8f9ff, #e8f4fd);
            border: 1px solid #e1e8ed; border-radius: 10px; padding: 20px; margin-bottom: 15px;
        }
        
        .instructions { 
            background: linear-gradient(135deg, #e3f2fd, #f8f9ff);
            padding: 20px; border-radius: 10px; margin-bottom: 20px; border: 1px solid #e1e8ed;
        }
        .instructions h3 { color: #1976d2; margin-bottom: 15px; }
        .instructions ol { margin-left: 20px; }
        .instructions li { margin-bottom: 10px; line-height: 1.6; }
        
        @media (max-width: 768px) {
            .main-content { grid-template-columns: 1fr; gap: 20px; padding: 20px; }
        }
    </style>
    <script src="https://api.qrserver.com/v1/create-qr-code/?size=1x1&data=test" style="display:none;"></script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📱 WhatsApp Scheduler</h1>
            <p>Free messaging automation using WhatsApp Web</p>
        </div>

        <div class="status-section">
            <div id="connectionStatus" class="status-indicator connecting">
                🔄 Starting WhatsApp Web connection...
            </div>
            
            <div id="setupProgress" style="display: none;">
                <div style="background: #e3f2fd; padding: 15px; border-radius: 10px; margin-bottom: 15px;">
                    <h4>🔧 Setup Progress:</h4>
                    <div id="progressText">Initializing...</div>
                    <div style="background: #ddd; border-radius: 10px; height: 8px; margin-top: 10px;">
                        <div id="progressBar" style="background: #25D366; height: 100%; border-radius: 10px; width: 0%; transition: width 0.3s;"></div>
                    </div>
                </div>
            </div>
            
            <div id="qrSection" class="qr-section" style="display: none;">
                <div class="instructions">
                    <h3>📱 Connect Your WhatsApp</h3>
                    <ol>
                        <li>Open WhatsApp on your phone</li>
                        <li>Go to Settings → Linked Devices</li>
                        <li>Tap "Link a Device"</li>
                        <li>Scan the QR code below</li>
                        <li>Start scheduling messages!</li>
                    </ol>
                </div>
                <div id="qrCode" class="qr-code">
                    <p>Generating QR Code...</p>
                </div>
            </div>
        </div>

        <div class="main-content" id="mainContent" style="display: none;">
            <div class="form-section">
                <h2>🗓️ Schedule New Message</h2>
                
                <div id="status"></div>

                <form id="scheduleForm">
                    <div class="form-group">
                        <label for="contact">Select Contact:</label>
                        <select id="contact" required>
                            <option value="">Loading contacts...</option>
                        </select>
                    </div>

                    <div class="form-group">
                        <label for="message">Message:</label>
                        <textarea id="message" placeholder="Enter your message here..." required></textarea>
                    </div>

                    <div class="form-group">
                        <label for="scheduleType">Schedule Type:</label>
                        <select id="scheduleType" required>
                            <option value="once">Send Once</option>
                            <option value="daily">Daily</option>
                            <option value="weekly">Weekly</option>
                        </select>
                    </div>

                    <div class="form-group">
                        <label for="datetime">Date & Time:</label>
                        <input type="datetime-local" id="datetime" required>
                    </div>

                    <button type="submit" class="btn" id="scheduleBtn">
                        🕒 Schedule Message
                    </button>
                </form>

                <div style="margin-top: 20px;">
                    <button id="sendNowBtn" class="btn btn-secondary">
                        ⚡ Send Immediately
                    </button>
                </div>
            </div>

            <div class="form-section">
                <h2>📋 Scheduled Messages</h2>
                
                <div id="messagesList">
                    <p style="text-align: center; color: #666; padding: 20px;">Loading scheduled messages...</p>
                </div>

                <div style="margin-top: 20px;">
                    <button onclick="refreshData()" class="btn btn-secondary" style="width: 100%;">
                        🔄 Refresh Data
                    </button>
                </div>
            </div>
        </div>
    </div>

    <script>
        let isWhatsAppReady = false;
        let setupInProgress = false;
        
        // Set minimum datetime to current time
        const now = new Date();
        now.setMinutes(now.getMinutes() + 1);
        document.getElementById('datetime').min = now.toISOString().slice(0, 16);

        // Update setup progress
        function updateProgress(text, percentage) {
            document.getElementById('setupProgress').style.display = 'block';
            document.getElementById('progressText').textContent = text;
            document.getElementById('progressBar').style.width = percentage + '%';
        }

        // Check WhatsApp status
        async function checkWhatsAppStatus() {
            try {
                const response = await fetch('/api/whatsapp/status');
                const data = await response.json();
                
                const statusElement = document.getElementById('connectionStatus');
                const qrSection = document.getElementById('qrSection');
                const mainContent = document.getElementById('mainContent');
                const setupProgress = document.getElementById('setupProgress');
                
                if (data.error && data.error.includes('Node.js server not ready')) {
                    if (!setupInProgress) {
                        setupInProgress = true;
                        statusElement.className = 'status-indicator connecting';
                        statusElement.innerHTML = '⚙️ Setting up Node.js environment...';
                        updateProgress('Installing Node.js dependencies...', 20);
                    }
                    return;
                }
                
                if (data.ready) {
                    isWhatsAppReady = true;
                    statusElement.className = 'status-indicator connected';
                    statusElement.innerHTML = '✅ WhatsApp Connected & Ready!';
                    qrSection.style.display = 'none';
                    setupProgress.style.display = 'none';
                    mainContent.style.display = 'grid';
                    
                    loadContacts();
                    loadMessages();
                    
                } else if (data.qr) {
                    isWhatsAppReady = false;
                    statusElement.className = 'status-indicator connecting';
                    statusElement.innerHTML = '📱 Scan QR Code to Connect';
                    qrSection.style.display = 'block';
                    setupProgress.style.display = 'none';
                    mainContent.style.display = 'none';
                    
                    displayQRCode(data.qr);
                    
                } else if (data.state) {
                    setupInProgress = true;
                    const stateMessages = {
                        'INITIALIZING': { text: '🔄 Initializing WhatsApp Web...', progress: 40 },
                        'AUTHENTICATED': { text: '🔐 Authentication successful...', progress: 70 },
                        'QR_RECEIVED': { text: '📱 QR Code ready for scanning...', progress: 60 },
                        'READY': { text: '✅ WhatsApp Web ready!', progress: 100 }
                    };
                    
                    const stateInfo = stateMessages[data.state] || { text: 'Starting up...', progress: 10 };
                    statusElement.className = 'status-indicator connecting';
                    statusElement.innerHTML = stateInfo.text;
                    updateProgress(stateInfo.text, stateInfo.progress);
                }
                
                updateButtonStates();
                
            } catch (error) {
                console.error('Status check error:', error);
                document.getElementById('connectionStatus').className = 'status-indicator disconnected';
                document.getElementById('connectionStatus').innerHTML = '❌ Connection Error - Check console for details';
            }
        }

        // Display QR code
        function displayQRCode(qrData) {
            const qrElement = document.getElementById('qrCode');
            const qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=${encodeURIComponent(qrData)}`;
            
            qrElement.innerHTML = `
                <img src="${qrUrl}" alt="WhatsApp QR Code" style="max-width: 100%; border-radius: 10px;" onerror="this.src='data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjUwIiBoZWlnaHQ9IjI1MCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMTAwJSIgaGVpZ2h0PSIxMDAlIiBmaWxsPSIjZjBmMGYwIi8+PHRleHQgeD0iNTAlIiB5PSI1MCUiIGZvbnQtZmFtaWx5PSJBcmlhbCIgZm9udC1zaXplPSIxNiIgZmlsbD0iIzY2NiIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZHk9Ii4zZW0iPk5vIFFSIENvZGU8L3RleHQ+PC9zdmc+'">
                <p style="margin-top: 15px; font-size: 0.9rem; color: #666;">
                    Scan this QR code with WhatsApp on your phone
                </p>
                <button onclick="refreshQR()" style="margin-top: 10px; padding: 10px 20px; background: #25D366; color: white; border: none; border-radius: 5px; cursor: pointer;">
                    🔄 Refresh QR Code
                </button>
            `;
        }

        // Refresh QR code
        function refreshQR() {
            document.getElementById('qrCode').innerHTML = '<p>Refreshing QR Code...</p>';
            checkWhatsAppStatus();
        }

        // Update button states
        function updateButtonStates() {
            const scheduleBtn = document.getElementById('scheduleBtn');
            const sendNowBtn = document.getElementById('sendNowBtn');
            
            if (isWhatsAppReady) {
                scheduleBtn.disabled = false;
                sendNowBtn.disabled = false;
                scheduleBtn.textContent = '🕒 Schedule Message';
                sendNowBtn.textContent = '⚡ Send Immediately';
            } else {
                scheduleBtn.disabled = true;
                sendNowBtn.disabled = true;
                scheduleBtn.textContent = '⏳ WhatsApp Not Ready';
                sendNowBtn.textContent = '⏳ WhatsApp Not Ready';
            }
        }

        // Load contacts
        // Load contacts
        async function loadContacts() {
            try {
                const response = await fetch('/api/contacts');
                const contacts = await response.json();
                
                const contactSelect = document.getElementById('contact');

                // Save current selection before refreshing
                const previousValue = contactSelect.value;

                // Clear and rebuild the options
                contactSelect.innerHTML = '<option value="">Choose a contact...</option>';
                
                Object.entries(contacts).forEach(([name, phone]) => {
                    const option = document.createElement('option');
                    option.value = JSON.stringify({name, phone});
                    option.textContent = `${name} (${phone})`;
                    contactSelect.appendChild(option);
                });

                if (Object.keys(contacts).length === 0) {
                    contactSelect.innerHTML = '<option value="">No contacts found - they will load automatically</option>';
                }

                // Restore previous selection if still available
                if (previousValue && [...contactSelect.options].some(opt => opt.value === previousValue)) {
                    contactSelect.value = previousValue;
                }

            } catch (error) {
                console.error('Failed to load contacts:', error);
                showStatus('Failed to load contacts', 'error');
            }
        }


        // Load messages
        async function loadMessages() {
            try {
                const response = await fetch('/api/messages');
                const messages = await response.json();
                
                const messagesList = document.getElementById('messagesList');
                
                if (messages.length === 0) {
                    messagesList.innerHTML = '<p style="text-align: center; color: #666; padding: 20px;">No scheduled messages yet</p>';
                    return;
                }

                messagesList.innerHTML = messages.map(msg => `
                    <div class="message-card">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                            <strong>${msg.contact_name}</strong>
                            <div style="display: flex; gap: 10px; align-items: center;">
                                <span style="background: rgba(37, 211, 102, 0.1); padding: 5px 10px; border-radius: 15px; font-size: 0.9rem;">
                                    ${new Date(msg.datetime).toLocaleString()}
                                </span>
                                ${msg.status === 'scheduled' ? `
                                    <button onclick="cancelMessage('${msg.id}')" style="padding: 5px 10px; background: #ff4757; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 0.8rem;">
                                        ❌ Cancel
                                    </button>
                                ` : ''}
                            </div>
                        </div>
                        <div style="color: #555; margin-bottom: 10px;">
                            ${msg.message.substring(0, 100)}${msg.message.length > 100 ? '...' : ''}
                        </div>
                        <div style="color: #888; font-size: 0.8rem;">
                            Type: ${msg.schedule_type} • Status: <span style="color: ${getStatusColor(msg.status)}; font-weight: bold;">${msg.status}</span> • Phone: ${msg.phone_number}
                        </div>
                    </div>
                `).join('');
            } catch (error) {
                console.error('Failed to load messages:', error);
                document.getElementById('messagesList').innerHTML = '<p style="color: red; text-align: center;">Failed to load messages</p>';
            }
        }

        // Get status color
        function getStatusColor(status) {
            switch(status) {
                case 'sent': return '#28a745';
                case 'failed': return '#dc3545';
                case 'scheduled': return '#007bff';
                default: return '#6c757d';
            }
        }

        // Show status
        function showStatus(message, type) {
            const statusDiv = document.getElementById('status');
            statusDiv.innerHTML = `<div class="status ${type}">${message}</div>`;
            setTimeout(() => statusDiv.innerHTML = '', 5000);
        }

        // Refresh data
        function refreshData() {
            loadContacts();
            loadMessages();
            showStatus('Data refreshed!', 'success');
        }

        // Schedule message form
        document.getElementById('scheduleForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            if (!isWhatsAppReady) {
                showStatus('WhatsApp is not connected. Please wait for connection.', 'error');
                return;
            }
            
            const contactValue = document.getElementById('contact').value;
            if (!contactValue) {
                showStatus('Please select a contact', 'error');
                return;
            }
            
            const contactData = JSON.parse(contactValue);
            const message = document.getElementById('message').value;
            const scheduleType = document.getElementById('scheduleType').value;
            const datetime = document.getElementById('datetime').value;

            try {
                const response = await fetch('/api/schedule', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        contact_name: contactData.name,
                        phone_number: contactData.phone,
                        message: message,
                        schedule_type: scheduleType,
                        datetime: datetime
                    })
                });

                const result = await response.json();
                
                if (result.success) {
                    showStatus('Message scheduled successfully!', 'success');
                    document.getElementById('scheduleForm').reset();
                    const newNow = new Date();
                    newNow.setMinutes(newNow.getMinutes() + 1);
                    document.getElementById('datetime').min = newNow.toISOString().slice(0, 16);
                    loadMessages();
                } else {
                    showStatus(`Failed to schedule: ${result.error}`, 'error');
                }
            } catch (error) {
                showStatus('Network error occurred', 'error');
                console.error('Schedule error:', error);
            }
        });

        // Send immediately
        document.getElementById('sendNowBtn').addEventListener('click', async () => {
            if (!isWhatsAppReady) {
                showStatus('WhatsApp is not connected. Please wait for connection.', 'error');
                return;
            }
            
            const contactValue = document.getElementById('contact').value;
            const message = document.getElementById('message').value;
            
            if (!contactValue || !message) {
                showStatus('Please select contact and enter message', 'error');
                return;
            }

            const contactData = JSON.parse(contactValue);

            try {
                const response = await fetch('/api/send', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        phone_number: contactData.phone,
                        message: message
                    })
                });

                const result = await response.json();
                
                if (result.success) {
                    showStatus(`Message sent immediately to ${contactData.name}!`, 'success');
                    document.getElementById('message').value = '';
                } else {
                    showStatus(`Failed to send: ${result.error}`, 'error');
                }
            } catch (error) {
                showStatus('Network error occurred', 'error');
                console.error('Send error:', error);
            }
        });

        // Cancel message
        async function cancelMessage(messageId) {
            if (confirm('Are you sure you want to cancel this scheduled message?')) {
                try {
                    const response = await fetch(`/api/cancel/${messageId}`, { method: 'DELETE' });
                    const result = await response.json();
                    
                    if (result.success) {
                        showStatus('Message cancelled successfully!', 'success');
                        loadMessages();
                    } else {
                        showStatus('Failed to cancel message', 'error');
                    }
                } catch (error) {
                    showStatus('Network error occurred', 'error');
                    console.error('Cancel error:', error);
                }
            }
        }

        // Start status checking
        checkWhatsAppStatus();
        setInterval(checkWhatsAppStatus, 5000);
        
        // Auto-refresh data
        setInterval(() => {
            if (isWhatsAppReady) {
                loadContacts();
                loadMessages();
            }
        }, 30000);
    </script>
</body>
</html>
        """


@app.route("/api/whatsapp/status")
def get_whatsapp_status():
    """Get WhatsApp connection status"""
    return jsonify(scheduler_instance.get_whatsapp_status())


@app.route("/api/contacts")
def get_contacts():
    """Get all contacts"""
    db_contacts = scheduler_instance.db.get_contacts()
    return jsonify(db_contacts)


@app.route("/api/messages", methods=["GET"])
def get_messages():
    """Get all scheduled messages"""
    messages = scheduler_instance.get_scheduled_messages()
    return jsonify([asdict(msg) for msg in messages])


@app.route("/api/schedule", methods=["POST"])
def schedule_message():
    """Schedule a new message"""
    data = request.json
    try:
        schedule_time = datetime.fromisoformat(data["datetime"])
        message_id = scheduler_instance.schedule_message(
            contact_name=data["contact_name"],
            phone_number=data["phone_number"],
            message=data["message"],
            schedule_time=schedule_time,
            schedule_type=data.get("schedule_type", "once"),
            file_path=data.get("file_path"),
        )

        if message_id:
            return jsonify({"success": True, "message_id": message_id})
        else:
            return jsonify({"success": False, "error": "Failed to schedule message"}), 400

    except Exception as e:
        logger.error(f"API schedule error: {e}")
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/send", methods=["POST"])
def send_immediately():
    """Send message immediately"""
    data = request.json
    try:
        success = scheduler_instance.send_immediately(
            phone_number=data["phone_number"],
            message=data["message"],
            file_path=data.get("file_path"),
        )
        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"API send error: {e}")
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/cancel/<message_id>", methods=["DELETE"])
def cancel_message(message_id):
    """Cancel a scheduled message"""
    success = scheduler_instance.cancel_scheduled_message(message_id)
    return jsonify({"success": success})


@app.route("/api/contacts", methods=["POST"])
def add_contact():
    """Add a new contact"""
    data = request.json
    success = scheduler_instance.db.add_contact(data["name"], data["phone_number"])
    return jsonify({"success": success})


def signal_handler(sig, frame):
    """Handle shutdown signals"""
    logger.info("Received shutdown signal")
    if scheduler_instance:
        scheduler_instance.shutdown()
    sys.exit(0)


def main():
    """Main function"""
    global scheduler_instance

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("""
🚀 WhatsApp Scheduler Starting...

📋 FEATURES:
✅ Free WhatsApp Web automation (no paid APIs)
✅ Node.js + whatsapp-web.js integration  
✅ Automatic contact syncing
✅ Message scheduling (once, daily, weekly)
✅ Persistent WhatsApp session
✅ Web interface at http://localhost:5000

🔧 The app will automatically:
- Install Node.js dependencies
- Start WhatsApp Web client
- Generate QR code for phone pairing
- Sync your WhatsApp contacts

📱 Once the QR code appears, scan it with your phone to connect!
    """)

    try:
        scheduler_instance = WhatsAppScheduler()
        app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        if scheduler_instance:
            scheduler_instance.shutdown()
    except Exception as e:
        logger.error(f"Application error: {e}")
        if scheduler_instance:
            scheduler_instance.shutdown()


if __name__ == "__main__":
    main()