# CSRF Token Template Examples

All forms in your application need to include CSRF tokens. Here are examples for each form type.

## 1. Login Form (templates/login.html)

```html
<form method="POST" action="{{ url_for('login') }}">
    {{ csrf_token() }}
    
    <div class="mb-3">
        <label for="username" class="form-label">Username</label>
        <input type="text" class="form-control" id="username" name="username" required>
    </div>
    
    <div class="mb-3">
        <label for="password" class="form-label">Password</label>
        <input type="password" class="form-control" id="password" name="password" required>
    </div>
    
    <button type="submit" class="btn btn-primary">Login</button>
</form>
```

## 2. Registration Form (templates/register.html)

```html
<form method="POST" action="{{ url_for('register') }}">
    {{ csrf_token() }}
    
    <div class="mb-3">
        <label for="username" class="form-label">Username</label>
        <input type="text" class="form-control" id="username" name="username" required>
    </div>
    
    <div class="mb-3">
        <label for="email" class="form-label">Email</label>
        <input type="email" class="form-control" id="email" name="email" required>
    </div>
    
    <div class="mb-3">
        <label for="password" class="form-label">Password</label>
        <input type="password" class="form-control" id="password" name="password" required>
        <small class="form-text text-muted">
            Password must be at least 8 characters with uppercase, lowercase, number, and special character.
        </small>
    </div>
    
    <div class="mb-3">
        <label for="confirm_password" class="form-label">Confirm Password</label>
        <input type="password" class="form-control" id="confirm_password" name="confirm_password" required>
    </div>
    
    <button type="submit" class="btn btn-primary">Register</button>
</form>
```

## 3. Add to Cart Form (templates/product.html)

```html
<form method="POST" action="{{ url_for('add_to_cart', product_id=product.id) }}">
    {{ csrf_token() }}
    
    <div class="input-group mb-3">
        <input type="number" class="form-control" name="quantity" value="1" min="1" max="99">
        <button class="btn btn-success" type="submit">
            <i class="fas fa-shopping-cart"></i> Add to Cart
        </button>
    </div>
</form>
```

## 4. Checkout Form (templates/checkout.html)

```html
<form method="POST" action="{{ url_for('checkout') }}" id="checkout-form">
    {{ csrf_token() }}
    
    <h4>Shipping Information</h4>
    
    <div class="mb-3">
        <label for="phone" class="form-label">M-Pesa Phone Number</label>
        <input type="tel" class="form-control" id="phone" name="phone" required>
    </div>
    
    <div class="mb-3">
        <label for="shipping_address" class="form-label">Shipping Address</label>
        <textarea class="form-control" id="shipping_address" name="shipping_address" rows="3" required></textarea>
    </div>
    
    <div class="mb-3">
        <label for="shipping_country" class="form-label">Country</label>
        <input type="text" class="form-control" id="shipping_country" name="shipping_country" required>
    </div>
    
    <div class="form-check mb-3">
        <input class="form-check-input" type="checkbox" id="agree" required>
        <label class="form-check-label" for="agree">
            I agree to the Terms & Conditions
        </label>
    </div>
    
    <button type="submit" class="btn btn-primary" id="pay-button">
        Proceed to Payment
    </button>
</form>
```

## 5. Admin Product Add/Edit Form (templates/admin/add_product.html)

```html
<form method="POST" action="{{ url_for('admin_add_product') }}" enctype="multipart/form-data">
    {{ csrf_token() }}
    
    <div class="mb-3">
        <label for="name" class="form-label">Product Name</label>
        <input type="text" class="form-control" id="name" name="name" required>
    </div>
    
    <div class="mb-3">
        <label for="description" class="form-label">Description</label>
        <textarea class="form-control" id="description" name="description" rows="4" required></textarea>
    </div>
    
    <div class="mb-3">
        <label for="category_id" class="form-label">Category</label>
        <select class="form-control" id="category_id" name="category_id">
            <option value="">Select Category</option>
            {% for category in categories %}
            <option value="{{ category.id }}">{{ category.name }}</option>
            {% endfor %}
        </select>
    </div>
    
    <div class="row">
        <div class="col-md-6 mb-3">
            <label for="buying_price" class="form-label">Buying Price</label>
            <input type="number" step="0.01" class="form-control" id="buying_price" name="buying_price" required>
        </div>
        
        <div class="col-md-6 mb-3">
            <label for="selling_price" class="form-label">Selling Price</label>
            <input type="number" step="0.01" class="form-control" id="selling_price" name="selling_price" required>
        </div>
    </div>
    
    <div class="mb-3">
        <label for="image" class="form-label">Product Image</label>
        <input type="file" class="form-control" id="image" name="image" accept="image/*">
        <small class="form-text text-muted">
            Allowed formats: PNG, JPG, JPEG, GIF, WebP (max 5MB)
        </small>
    </div>
    
    <div class="form-check mb-3">
        <input class="form-check-input" type="checkbox" id="is_digital" name="is_digital">
        <label class="form-check-label" for="is_digital">
            Digital Product
        </label>
    </div>
    
    <button type="submit" class="btn btn-primary">Add Product</button>
</form>
```

## 6. Feedback Form (in base.html or support.html)

```html
<form method="POST" action="{{ url_for('customer_feedback') }}">
    {{ csrf_token() }}
    
    <div class="mb-3">
        <label class="form-label">Experience Rating</label>
        <div class="rating">
            {% for i in range(1, 6) %}
            <input type="radio" id="exp{{ i }}" name="experience_rating" value="{{ i }}" {% if i == 5 %}checked{% endif %}>
            <label for="exp{{ i }}">{{ i }} stars</label>
            {% endfor %}
        </div>
    </div>
    
    <div class="mb-3">
        <label class="form-label">Satisfaction Rating</label>
        <div class="rating">
            {% for i in range(1, 6) %}
            <input type="radio" id="sat{{ i }}" name="satisfaction_rating" value="{{ i }}" {% if i == 5 %}checked{% endif %}>
            <label for="sat{{ i }}">{{ i }} stars</label>
            {% endfor %}
        </div>
    </div>
    
    <div class="mb-3">
        <label for="improvement_text" class="form-label">How can we improve?</label>
        <textarea class="form-control" id="improvement_text" name="improvement_text" rows="3"></textarea>
    </div>
    
    <button type="submit" class="btn btn-primary">Submit Feedback</button>
</form>
```

## 7. Review Submission Form (for AJAX)

If using JavaScript/AJAX to submit forms, you need to include the CSRF token in the request:

```html
<script>
function submitReview(orderId, productId) {
    const rating = document.querySelector('input[name="rating"]:checked').value;
    const comment = document.getElementById('comment').value;
    const csrfToken = document.querySelector('input[name="csrf_token"]').value;
    
    fetch(`/api/review/${orderId}/${productId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken  // Include CSRF token in header
        },
        body: JSON.stringify({
            rating: rating,
            comment: comment
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Review submitted successfully!');
        }
    })
    .catch(error => console.error('Error:', error));
}
</script>

<!-- Form with CSRF token -->
<form id="review-form" onsubmit="event.preventDefault(); submitReview({{ order.id }}, {{ product.id }});">
    {{ csrf_token() }}
    
    <div class="mb-3">
        <label class="form-label">Rating</label>
        <div class="rating-stars">
            {% for i in range(1, 6) %}
            <input type="radio" id="star{{ i }}" name="rating" value="{{ i }}">
            <label for="star{{ i }}">⭐</label>
            {% endfor %}
        </div>
    </div>
    
    <div class="mb-3">
        <label for="comment" class="form-label">Comment</label>
        <textarea class="form-control" id="comment" name="comment" rows="3"></textarea>
    </div>
    
    <button type="submit" class="btn btn-primary">Submit Review</button>
</form>
```

## 8. Admin Delete Actions (with confirmation)

```html
<!-- Product delete button -->
<form method="POST" action="{{ url_for('admin_delete_product', pid=product.id) }}" 
      onsubmit="return confirm('Are you sure you want to delete this product?');">
    {{ csrf_token() }}
    <button type="submit" class="btn btn-danger btn-sm">
        <i class="fas fa-trash"></i> Delete
    </button>
</form>

<!-- User deactivate button -->
<form method="POST" action="{{ url_for('admin_toggle_user', uid=user.id) }}" class="d-inline">
    {{ csrf_token() }}
    <button type="submit" class="btn btn-warning btn-sm">
        {{ 'Activate' if not user.is_active else 'Deactivate' }}
    </button>
</form>
```

## 9. Cart Update/Remove Actions

```html
<!-- Update cart quantity -->
<form method="POST" action="{{ url_for('update_cart', item_id=item.id) }}" class="d-inline">
    {{ csrf_token() }}
    <input type="number" name="quantity" value="{{ item.quantity }}" min="1" max="99" style="width: 60px;">
    <button type="submit" class="btn btn-sm btn-primary">Update</button>
</form>

<!-- Remove from cart -->
<form method="POST" action="{{ url_for('remove_cart_item', item_id=item.id) }}" class="d-inline">
    {{ csrf_token() }}
    <button type="submit" class="btn btn-sm btn-danger">
        <i class="fas fa-times"></i> Remove
    </button>
</form>
```

## 10. Category Follow/Unfollow

```html
<form method="POST" action="{{ url_for('follow_category', category_id=category.id) }}">
    {{ csrf_token() }}
    <button type="submit" class="btn btn-outline-success btn-sm">
        <i class="fas fa-bell"></i> 
        {{ 'Unfollow' if is_following else 'Follow' }} {{ category.name }}
    </button>
</form>
```

## Important Notes

1. **NEVER skip CSRF tokens** - All POST forms must include `{{ csrf_token() }}`

2. **AJAX requests** need to include the token in headers:
   ```javascript
   headers: {
       'X-CSRFToken': document.querySelector('input[name="csrf_token"]').value
   }
   ```

3. **jQuery AJAX** example:
   ```javascript
   $.ajax({
       type: 'POST',
       url: '/api/endpoint',
       data: JSON.stringify({key: 'value'}),
       contentType: 'application/json',
       headers: {
           'X-CSRFToken': $('input[name="csrf_token"]').val()
       }
   });
   ```

4. **Meta tag method** (alternative for AJAX):
   Add to base.html `<head>`:
   ```html
   <meta name="csrf-token" content="{{ csrf_token() }}">
   ```
   
   Then in JavaScript:
   ```javascript
   const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
   ```

5. **Testing CSRF protection**:
   - Try submitting a form without the CSRF token
   - Should get "400 Bad Request: The CSRF token is missing"
   - This confirms protection is working

## Quick Find & Replace

To add CSRF tokens to existing forms:

1. Search for: `<form method="POST"`
2. Replace with: 
   ```html
   <form method="POST"
   ```
3. Then add on next line: `{{ csrf_token() }}`

Or use this regex pattern:
```
Find: (<form method="POST"[^>]*>)
Replace: $1\n    {{ csrf_token() }}
```

## Verification

After adding CSRF tokens, test each form:
1. Submit the form normally - should work
2. Remove `{{ csrf_token() }}` and submit - should fail with 400 error
3. Add it back - should work again

This confirms CSRF protection is active and working correctly.
