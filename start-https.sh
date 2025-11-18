#!/bin/bash

echo "🔒 Setting up HTTPS for Dograh..."

# Generate SSL certificates if they don't exist
if [ ! -f "ssl/cert.pem" ] || [ ! -f "ssl/key.pem" ]; then
    echo "📜 Generating SSL certificates..."
    chmod +x generate-certs.sh
    ./generate-certs.sh
else
    echo "✅ SSL certificates already exist"
fi

# Start the services
echo "🚀 Starting Dograh with HTTPS..."
docker-compose up -d

# Wait a moment for services to start
sleep 3

# Show the URLs
echo ""
echo "🎉 Dograh is starting with HTTPS!"
echo "📱 UI: https://$(hostname -I | awk '{print $1}'):3010"
echo "🔧 API: https://$(hostname -I | awk '{print $1}'):8000"
echo ""
echo "⚠️  You'll see a security warning in your browser because we're using self-signed certificates."
echo "   Just click 'Advanced' → 'Proceed to site' and the microphone will work!"
echo ""
echo "📊 Monitor services:"
echo "   docker-compose logs -f"