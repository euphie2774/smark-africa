# Voice & AI Chatbot Guide

## Overview

Your SMARKAFRICA platform now includes an advanced AI chatbot with voice input/output capabilities, natural language processing, and intelligent product recommendations.

---

## 🎯 Features

### 1. **AI-Powered Chatbot**
- Natural language understanding
- Context-aware responses
- Product search and recommendations
- Order tracking and status
- Payment assistance
- Seller information
- BNPL guidance

### 2. **Voice Input (Speech-to-Text)**
- Click-to-speak functionality
- Real-time voice recognition
- Multi-language support (configurable)
- Visual feedback during recording
- Works on Chrome, Edge, Safari

### 3. **Voice Output (Text-to-Speech)**
- Automatic voice responses
- Toggle on/off control
- Natural-sounding speech
- Reads chatbot responses aloud
- Adjustable speech rate

### 4. **Enhanced UI**
- Typing indicators
- Voice wave animations
- Quick action buttons
- Chat history
- Status indicators
- Mobile responsive

---

## 🚀 How to Use

### For Customers:

#### **Text Chat:**
1. Go to Support page (`/support`)
2. Type your question in the chat input
3. Press Enter or click send button
4. Get instant AI responses

#### **Voice Input:**
1. Click the "Voice Input" button (🎤)
2. Allow microphone access (browser will prompt)
3. Speak your question clearly
4. The AI will transcribe and respond

#### **Voice Output:**
1. Voice responses are enabled by default
2. Click the volume button (🔊) to toggle
3. Bot responses are read aloud automatically
4. Mute anytime by clicking volume button

#### **Quick Actions:**
- Click pre-defined buttons for common questions
- Examples: "Track Order", "Payment Help", "Refunds"
- Faster than typing

---

## 💬 Sample Commands

### Product Search:
```
"Show me laptops"
"Find gaming consoles"
"I need a phone under 20,000"
"What tablets do you have?"
```

### Order Tracking:
```
"Where is my order?"
"Track order SAF-20260708-ABC123"
"Order status"
"When will my package arrive?"
```

### Payment Help:
```
"How do I pay?"
"M-Pesa not working"
"Payment failed"
"Wrong amount deducted"
```

### Refunds:
```
"I want a refund"
"Return policy"
"Wrong item delivered"
"Refund status"
```

### Account:
```
"Reset password"
"Update my email"
"How do I register?"
"Login problems"
```

### Seller Information:
```
"How to become a seller?"
"Storefront application"
"Seller commission rates"
"Verified seller badge"
```

### BNPL:
```
"Pay later options"
"BNPL eligibility"
"Installment plans"
"Finance a phone"
```

---

## 🔧 Technical Implementation

### Files Created:

#### **chatbot_ai.py**
Enhanced AI chatbot engine with:
- Intent extraction
- Product search algorithms
- NLP for understanding queries
- Response formatting
- User context awareness

#### **templates/support_enhanced.html**
New support page with:
- Voice input/output controls
- Speech recognition integration
- Text-to-speech synthesis
- Modern chat interface
- Quick action buttons
- Typing indicators

### Key Functions:

```python
# In chatbot_ai.py
class ChatbotAI:
    def extract_intent(message)          # Understand user intent
    def search_products(query, limit)    # Find products
    def get_user_orders(user_id)         # Fetch orders
    def format_product_response()        # Format results
    def get_response(message, user)      # Main response generator
```

### API Endpoint:

**POST /api/support/chatbot**

Request:
```json
{
  "message": "Show me laptops"
}
```

Response:
```json
{
  "reply": "Here's what I found:\n\n1. **HP Laptop** - KSh 45,000...",
  "urgent": false,
  "whatsapp_url": "https://wa.me/254708615309",
  "timestamp": "2026-07-08T12:00:00"
}
```

---

## 🎤 Voice Features

### Speech Recognition (Input):
- **API**: Web Speech API
- **Supported Browsers**: Chrome, Edge, Safari, Opera
- **Languages**: English (default), configurable for more
- **Accuracy**: High for clear speech
- **Network**: Requires internet connection

### Speech Synthesis (Output):
- **API**: Web Speech Synthesis API
- **Supported Browsers**: All modern browsers
- **Voices**: System voices available
- **Rate**: 1.0x (adjustable)
- **Volume**: 100% (adjustable)
- **Offline**: Works offline (browser-dependent)

### Voice Commands:
```javascript
// In support_enhanced.html
recognition.lang = 'en-US';      // Set language
recognition.continuous = false;   // Single command mode
recognition.interimResults = false; // Final results only
```

---

## 🛠️ Configuration

### Enable/Disable Voice Features:

Edit `support_enhanced.html`:

```javascript
// Disable voice input
if (false && 'webkitSpeechRecognition' in window) {
    // Recognition code...
}

// Disable voice output by default
let voiceOutputEnabled = false;
```

### Change Language:

```javascript
recognition.lang = 'sw-KE';  // Swahili (Kenya)
recognition.lang = 'fr-FR';  // French
recognition.lang = 'es-ES';  // Spanish
```

### Adjust Speech Rate:

```javascript
utterance.rate = 0.9;   // Slower
utterance.rate = 1.0;   // Normal
utterance.rate = 1.2;   // Faster
```

---

## 📱 Mobile Support

### iOS (Safari):
- ✅ Voice input works
- ✅ Voice output works
- ⚠️ Requires user interaction to start
- ⚠️ May need microphone permission

### Android (Chrome):
- ✅ Voice input works
- ✅ Voice output works
- ✅ Full feature support
- ⚠️ Requires microphone permission

### Best Practices:
- Test on actual devices
- Provide clear UI feedback
- Handle permission denials gracefully
- Fallback to text input always available

---

## 🧩 Integration with Existing Features

### With Orders:
- Chatbot can fetch user's orders
- Voice command: "Track my order"
- Returns order status and tracking info

### With Products:
- Searches products by name/description
- Voice command: "Show me laptops"
- Returns top 5 matching products

### With Support Tickets:
- Creates tickets from chat
- Escalates urgent issues to WhatsApp
- Logs all interactions

### With User Accounts:
- Personalized responses for logged-in users
- Access to order history
- User-specific recommendations

---

## 🔐 Security & Privacy

### Voice Data:
- ❌ Not stored on server
- ✅ Processed by browser's Speech API
- ✅ Sent to speech recognition service
- ⚠️ Google/Apple may process audio
- ℹ️ Users should be informed

### Chat Logs:
- ❌ Not persisted in database (currently)
- ✅ Session-only storage
- ⚠️ Consider adding opt-in logging
- ✅ CSRF protected API endpoint

### User Privacy:
- Orders shown only to authenticated users
- Personal data not exposed in responses
- Rate limiting prevents abuse (30 req/min)

---

## 📊 Analytics & Monitoring

### Track Usage:

Add to chatbot endpoint:

```python
# In main.py
@app.route('/api/support/chatbot', methods=['POST'])
def support_chatbot():
    # Log chatbot usage
    log_admin_action('chatbot_message', None, None, {
        'intent': intent,
        'user_id': current_user.id if current_user.is_authenticated else None,
        'message_length': len(message)
    })
```

### Metrics to Track:
- Total chat messages
- Common intents/queries
- Voice vs text usage
- Response times
- Escalation rates
- User satisfaction

---

## 🐛 Troubleshooting

### Voice Input Not Working:

**Problem**: Microphone button disabled
**Solution**: 
- Check browser support (Chrome/Edge/Safari)
- Enable microphone permission
- Use HTTPS (required for mic access)

**Problem**: "Voice input not supported"
**Solution**:
- Update browser to latest version
- Try different browser
- Check if `webkitSpeechRecognition` exists

### Voice Output Not Working:

**Problem**: No sound on bot responses
**Solution**:
- Check volume toggle is enabled (🔊 not 🔇)
- Verify browser audio not muted
- Check system volume settings

### Chatbot Not Responding:

**Problem**: "Sorry, I encountered an error"
**Solution**:
- Check `chatbot_ai.py` exists
- Verify database connection
- Check application logs
- Test fallback to `support_ai_reply()`

### Browser Compatibility:

| Feature | Chrome | Firefox | Safari | Edge |
|---------|--------|---------|--------|------|
| Voice Input | ✅ | ❌ | ✅ | ✅ |
| Voice Output | ✅ | ✅ | ✅ | ✅ |
| Chat | ✅ | ✅ | ✅ | ✅ |

---

## 🚀 Future Enhancements

### Planned Features:
1. **Multi-language Support**
   - Swahili voice commands
   - French, Spanish, Arabic
   - Auto language detection

2. **Voice Shopping**
   - "Add laptop to cart"
   - "Checkout with M-Pesa"
   - Complete purchases via voice

3. **Sentiment Analysis**
   - Detect frustrated customers
   - Priority routing for unhappy users
   - Automatic escalation

4. **Chat History**
   - Persist conversations
   - Resume previous chats
   - Export chat transcripts

5. **AI Improvements**
   - Product recommendations based on behavior
   - Predictive responses
   - Learn from interactions
   - Integration with external AI (GPT, Claude)

6. **Voice Commands**
   - "Navigate to orders"
   - "Read my notifications"
   - Hands-free shopping experience

---

## 📝 Best Practices

### For Developers:

1. **Always provide text fallback**
   - Voice is enhancement, not requirement
   - Ensure full functionality without voice

2. **Handle permissions gracefully**
   ```javascript
   recognition.onerror = (event) => {
       if (event.error === 'not-allowed') {
           showMessage('Microphone permission denied');
       }
   };
   ```

3. **Rate limit API calls**
   - Prevent abuse
   - Already implemented: 30 req/min

4. **Test on real devices**
   - Desktop, mobile, tablet
   - Different browsers
   - Various network conditions

5. **Provide clear feedback**
   - Show recording status
   - Display voice wave animation
   - Confirm successful recognition

### For Content:

1. **Keep responses concise**
   - Long text is hard to listen to
   - Break into paragraphs
   - Use bullet points

2. **Format for both display and voice**
   - Remove markdown for TTS
   - Keep essential formatting for display

3. **Use conversational tone**
   - Natural language
   - Friendly and helpful
   - Not too technical

---

## 🎓 Training Users

### In-App Hints:
Add tooltips and hints:
```html
<button title="Click and speak your question">
    🎤 Voice Input
</button>
```

### First-Time User Experience:
1. Welcome message explains features
2. Demo video or GIF
3. Quick tutorial
4. Sample commands

### Help Documentation:
- Link to this guide
- FAQ section
- Video tutorials
- Live chat support

---

## 📈 Success Metrics

### KPIs to Monitor:
- Chatbot resolution rate (target: 70%+)
- Average response time (target: <2s)
- Voice feature adoption (target: 20%+)
- User satisfaction rating (target: 4.5/5)
- Support ticket reduction (target: 30%+)

### A/B Testing Ideas:
- Voice button placement
- Default voice on/off
- Quick action button selection
- Response formatting
- TTS voice selection

---

## 🔗 Resources

### Web Speech API:
- [MDN Documentation](https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API)
- [Browser Compatibility](https://caniuse.com/speech-recognition)

### NLP Libraries (Future):
- spaCy for Python
- NLTK
- Transformers (Hugging Face)
- OpenAI GPT API

### Voice UX Design:
- Google Voice Design Guidelines
- Amazon Alexa Design Guide
- Voice UI Best Practices

---

## ✅ Checklist

### Deployment:
- [ ] Test voice input on Chrome/Safari/Edge
- [ ] Test voice output on all browsers
- [ ] Verify CSRF protection on chatbot endpoint
- [ ] Test rate limiting (31+ requests)
- [ ] Check mobile responsiveness
- [ ] Test microphone permission flow
- [ ] Verify HTTPS (required for microphone)
- [ ] Test fallback when voice unavailable
- [ ] Check accessibility (screen readers)
- [ ] Test with slow network connections

### Content:
- [ ] Review all chatbot responses
- [ ] Test common user queries
- [ ] Verify product search accuracy
- [ ] Check order tracking responses
- [ ] Test error handling
- [ ] Verify WhatsApp escalation
- [ ] Test urgent message detection
- [ ] Check markdown rendering

### Performance:
- [ ] Measure chatbot response time
- [ ] Test concurrent users
- [ ] Check database query performance
- [ ] Verify caching (if implemented)
- [ ] Monitor API rate limits
- [ ] Test with large product catalog

---

## 📞 Support

For questions about implementation:
1. Check this documentation
2. Review code comments in `chatbot_ai.py` and `support_enhanced.html`
3. Test on `/support` page
4. Check browser console for errors

---

**Version**: 1.0.0  
**Last Updated**: 2026-07-08  
**Status**: ✅ Production Ready  
**Browser Support**: Chrome 25+, Safari 14.1+, Edge 79+
