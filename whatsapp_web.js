
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
        