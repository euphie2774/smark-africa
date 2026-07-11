"""
Enhanced AI Chatbot System for SMARKAFRICA
Includes NLP, product recommendations, and voice support
"""
import re
from datetime import datetime, timedelta
from sqlalchemy import func


class ChatbotAI:
    """Enhanced AI chatbot with natural language processing"""

    def __init__(self, db, models):
        self.db = db
        self.Product = models['Product']
        self.Order = models['Order']
        self.Category = models['Category']
        self.User = models['User']

    def extract_intent(self, message):
        """Extract user intent from message"""
        message_lower = message.lower()

        intents = {
            'product_search': ['find', 'search', 'looking for', 'want to buy', 'need', 'show me'],
            'order_status': ['order', 'track', 'delivery', 'shipping', 'where is my'],
            'payment': ['payment', 'pay', 'mpesa', 'stk', 'receipt', 'transaction'],
            'refund': ['refund', 'return', 'money back', 'cancel order'],
            'bnpl': ['bnpl', 'pay later', 'installment', 'instalment', 'finance'],
            'account': ['account', 'profile', 'password', 'login', 'register'],
            'seller': ['seller', 'become seller', 'sell', 'storefront'],
            'help': ['help', 'how to', 'what is', 'explain'],
            'greeting': ['hi', 'hello', 'hey', 'good morning', 'good afternoon'],
            'price': ['price', 'cost', 'how much', 'cheap', 'expensive'],
            'compare': ['compare', 'difference', 'better', 'vs', 'versus'],
            'review': ['review', 'rating', 'feedback', 'opinion'],
        }

        for intent, keywords in intents.items():
            if any(keyword in message_lower for keyword in keywords):
                return intent

        return 'general'

    def extract_product_query(self, message):
        """Extract product search query from message"""
        message_lower = message.lower()

        # Remove common words
        stop_words = ['find', 'search', 'looking', 'for', 'want', 'to', 'buy', 'need',
                      'show', 'me', 'a', 'an', 'the', 'some']

        words = message_lower.split()
        query_words = [w for w in words if w not in stop_words and len(w) > 2]

        return ' '.join(query_words) if query_words else message

    def search_products(self, query, limit=5):
        """Search products by name, description, or category"""
        if not query:
            return []

        search_pattern = f"%{query}%"

        products = self.Product.query.filter(
            self.Product.is_active == True,
            (
                self.Product.name.ilike(search_pattern) |
                self.Product.description.ilike(search_pattern) |
                self.Product.short_description.ilike(search_pattern)
            )
        ).limit(limit).all()

        return products

    def get_popular_products(self, limit=5):
        """Get popular/trending products"""
        return self.Product.query.filter_by(
            is_active=True
        ).order_by(
            self.Product.sales_count.desc(),
            self.Product.views_count.desc()
        ).limit(limit).all()

    def get_user_orders(self, user_id, limit=3):
        """Get user's recent orders"""
        return self.Order.query.filter_by(
            user_id=user_id
        ).order_by(
            self.Order.created_at.desc()
        ).limit(limit).all()

    def format_product_response(self, products):
        """Format products into a readable response"""
        if not products:
            return "I couldn't find any products matching that. Try searching for electronics, fashion, or home items."

        response = "Here's what I found:\n\n"
        for i, product in enumerate(products, 1):
            price = product.discounted_price if product.discount_percent else product.selling_price
            response += f"{i}. **{product.name}** - KSh {price:,.0f}\n"
            if product.short_description:
                response += f"   {product.short_description[:80]}...\n"
            response += f"   View: /product/{product.slug}\n\n"

        return response.strip()

    def format_order_response(self, orders):
        """Format orders into a readable response"""
        if not orders:
            return "You don't have any orders yet. Start shopping to place your first order!"

        response = "Your recent orders:\n\n"
        for order in orders:
            status_emoji = {
                'pending': '⏳',
                'processing': '📦',
                'completed': '✅',
                'cancelled': '❌'
            }.get(order.status, '📋')

            response += f"{status_emoji} **Order {order.order_number}**\n"
            response += f"   Status: {order.status.title()}\n"
            response += f"   Amount: KSh {order.amount_paid:,.0f}\n"
            response += f"   Payment: {order.payment_status}\n"

            if order.shipping_status:
                response += f"   Shipping: {order.shipping_status}\n"

            if order.tracking_number:
                response += f"   Track: /track/{order.id}\n"

            response += "\n"

        return response.strip()

    def get_response(self, message, user=None):
        """Generate chatbot response based on message and context"""
        intent = self.extract_intent(message)

        # Greeting
        if intent == 'greeting':
            greeting = "Hi! 👋 I'm your SMARKAFRICA shopping assistant. "
            greeting += "I can help you find products, track orders, or answer questions. What would you like to do?"
            return greeting

        # Product search
        if intent == 'product_search':
            query = self.extract_product_query(message)
            products = self.search_products(query)

            if products:
                return self.format_product_response(products)
            else:
                # Show popular products as fallback
                popular = self.get_popular_products()
                response = f"I couldn't find '{query}', but here are some popular items:\n\n"
                response += self.format_product_response(popular)
                return response

        # Order status
        if intent == 'order_status':
            if user:
                orders = self.get_user_orders(user.id)
                return self.format_order_response(orders)
            else:
                return "Please log in to view your orders. You can track any order by going to 'My Orders' in your account."

        # Payment help
        if intent == 'payment':
            return """**Payment Help:**

✅ We accept M-Pesa via STK Push
✅ Enter your phone number at checkout
✅ Approve the payment prompt on your phone
✅ Payment is confirmed instantly

**Troubleshooting:**
- Didn't receive prompt? Check your phone signal
- Transaction failed? Try again or check M-Pesa balance
- Wrong amount deducted? Contact support with receipt

Need urgent help? WhatsApp: 0708615309"""

        # Refund
        if intent == 'refund':
            return """**Refund Process:**

1. Go to 'My Orders'
2. Click on the order
3. Click 'Request Refund'
4. Explain the reason
5. We'll review within 24-48 hours

**Refund Reasons:**
✓ Item not as described
✓ Damaged on arrival
✓ Wrong item received
✓ Delivery too late

Urgent refund? WhatsApp: 0708615309"""

        # BNPL
        if intent == 'bnpl':
            return """**Buy Now Pay Later (BNPL):**

📱 Finance eligible products over 3 months
💰 Pay 15% deposit upfront
📅 Automatic installments
🔒 Device may lock if overdue

**How to Apply:**
1. Find BNPL-eligible product
2. Click 'Pay Later' at checkout
3. Complete verification
4. Pay deposit to activate

Check BNPL status: /bnpl/apply"""

        # Account
        if intent == 'account':
            if user:
                return f"""**Your Account:** {user.username}

✓ Email: {user.email}
✓ Member since: {user.created_at.strftime('%B %Y')}
✓ Status: {user.seller_status.title()}

Manage account: /profile
Change password: /account/settings"""
            else:
                return """**Account Help:**

🔐 **Login** - Use your username and password
📝 **Register** - Create account in 2 minutes
🔑 **Forgot Password** - Use password reset

Need to create an account? Click 'Register' at the top."""

        # Seller
        if intent == 'seller':
            return """**Become a Seller:**

💼 Sell products on SMARKAFRICA
📦 Physical or digital products
💰 Competitive commission rates
✅ Get verified seller badge

**How to Apply:**
1. Complete seller verification
2. Upload ID and selfie
3. Wait for approval (24-48 hours)
4. Start listing products

Apply now: /seller/apply
Storefront: /storefront/apply"""

        # Price inquiry
        if intent == 'price':
            query = self.extract_product_query(message)
            products = self.search_products(query, limit=3)

            if products:
                response = "Here are the prices:\n\n"
                for product in products:
                    price = product.discounted_price if product.discount_percent else product.selling_price
                    response += f"• {product.name}: **KSh {price:,.0f}**"

                    if product.discount_percent:
                        response += f" (Save {product.discount_percent}%!)"

                    response += "\n"

                return response.strip()
            else:
                return f"I couldn't find prices for '{query}'. Try browsing our categories or be more specific."

        # Compare products
        if intent == 'compare':
            return """**Product Comparison:**

To compare products:
1. Browse products in the same category
2. Click 'Compare' button on product pages
3. Add 2-4 products to comparison
4. View side-by-side specs and prices

Or tell me which specific products you want to compare!"""

        # Reviews
        if intent == 'review':
            return """**Product Reviews:**

⭐ All reviews are from verified buyers
📝 Leave reviews after purchase
👍 Helpful reviews earn loyalty points

**To leave a review:**
1. Complete your order
2. Go to 'My Orders'
3. Click 'Review Product'
4. Rate and comment

View reviews on any product page!"""

        # Help
        if intent == 'help':
            return """**I Can Help With:**

🛍️ **Shopping** - Find products, compare prices
📦 **Orders** - Track delivery, order status
💳 **Payments** - M-Pesa, BNPL, refunds
👤 **Account** - Login, profile, seller status
🏪 **Selling** - Become seller, storefront
❓ **Support** - Any questions or issues

Just ask me anything or use voice commands!

**Quick Links:**
- Shop: /shop
- My Orders: /orders
- Track Order: /track
- Support: /support"""

        # General/fallback
        return """I'm here to help! I can assist with:

• **Finding products** - "Show me laptops"
• **Order tracking** - "Where is my order?"
• **Payment help** - "How do I pay?"
• **Account issues** - "Reset password"
• **Seller info** - "How to sell?"

What would you like to know?

For urgent help: WhatsApp 0708615309"""


def create_chatbot_instance(app):
    """Factory function to create chatbot instance"""
    from models import Product, Order, Category, User, db

    models = {
        'Product': Product,
        'Order': Order,
        'Category': Category,
        'User': User
    }

    return ChatbotAI(db, models)
