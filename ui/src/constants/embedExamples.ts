export const HEADLESS_CHAT_EXAMPLE = `let chatState = 'idle';

function withDograhWidget(callback) {
  if (window.DograhWidget) {
    callback(window.DograhWidget);
    return;
  }

  const script = document.getElementById('dograh-widget');
  if (!script) {
    console.error('Dograh embed script not found');
    return;
  }

  script.addEventListener('load', () => {
    if (window.DograhWidget) callback(window.DograhWidget);
  }, { once: true });
}

withDograhWidget((widget) => {
  widget.onChatStateChange((state) => {
    chatState = state; // idle | starting | ready | waiting | ended | expired | error
  });

  widget.onMessage((text, turn) => {
    appendAgentBubble(text); // render however you want
  });

  document.getElementById('open-chat').addEventListener('click', () => {
    widget.startChat();
  });

  document.getElementById('send-btn').addEventListener('click', async () => {
    const input = document.getElementById('chat-input');
    appendVisitorBubble(input.value);
    const transcript = await widget.sendMessage(input.value);
    if (transcript !== null) input.value = '';
  });

  document.getElementById('end-chat')?.addEventListener('click', async () => {
    await widget.endChat();
  });
});`;
