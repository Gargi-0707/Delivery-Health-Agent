const chatBox = document.getElementById('chat-box');
const userInput = document.getElementById('user-input');
const typingIndicator = document.getElementById('typing-indicator');

// The API key will be passed from the HTML template
let API_KEY = "";

function setApiKey(key) {
    API_KEY = key;
}

function appendMessage(role, content) {
    const div = document.createElement('div');
    div.className = `message ${role}-msg`;
    
    if (role === 'bot') {
        div.innerHTML = marked.parse(content);
    } else {
        div.textContent = content;
    }
    
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
}

async function ask(question) {
    userInput.value = question;
    sendMessage();
}

async function sendMessage() {
    const question = userInput.value.trim();
    if (!question) return;

    appendMessage('user', question);
    userInput.value = '';
    
    const suggestions = document.getElementById('suggestions');
    if (suggestions) suggestions.style.display = 'none';
    
    typingIndicator.style.display = 'block';

    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'x-api-key': API_KEY
            },
            body: JSON.stringify({ question })
        });

        if (response.status === 401) throw new Error('Unauthorized: Missing or invalid API Key');
        if (!response.ok) throw new Error('Failed to reach AI engine');

        const data = await response.json();
        appendMessage('bot', data.answer);
    } catch (error) {
        appendMessage('bot', '❌ Error: ' + error.message);
    } finally {
        typingIndicator.style.display = 'none';
    }
}
