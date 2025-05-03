 // WebSocket connection (only for IP address updates now)
        const websocket = new WebSocket('ws://' + window.location.hostname + ':8766');

        websocket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'ip_address') {
                    const messageDiv = document.getElementById('wifi-message');
                    const currentMessage = messageDiv.textContent;
                    messageDiv.textContent = `${currentMessage} Raspberry Pi IP: ${data.ip}`;
                    messageDiv.className = 'ip-address';
                }
            } catch (error) {
                console.error("Error processing WebSocket message:", error);
            }
        };

        document.getElementById('wifi-connect-btn').addEventListener('click', () => {
            const ssid = document.getElementById('wifi-ssid').value;
            const password = document.getElementById('wifi-password').value;
            const messageDiv = document.getElementById('wifi-message');
            messageDiv.textContent = "Attempting to connect...";
            messageDiv.className = 'info';

            fetch('/wifi/connect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ssid, password })
            })
            .then(response => {
                if (!response.ok) {
                    return response.json().then(errData => {
                        throw new Error(errData.error || `HTTP error! status: ${response.status}`);
                    });
                }
                return response.json();
            })
            .then(data => {
                messageDiv.textContent = data.message;
                messageDiv.className = data.success ? 'success' : 'error';
                // Optionally refresh the Wi-Fi list after a connection attempt
                // setTimeout(populateWifiList, 5000);
            })
            .catch(error => {
                console.error('Error connecting to Wi-Fi:', error);
                messageDiv.textContent = error.message || 'Failed to connect to Wi-Fi.';
                messageDiv.className = 'error';
            });
        });
