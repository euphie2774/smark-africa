// SMARKAFRICA Main JavaScript

document.addEventListener('DOMContentLoaded', function() {

    document.querySelectorAll('.phone-country-select').forEach(function(select) {
        const group = select.closest('.phone-code-group');
        const flag = group ? group.querySelector('.phone-country-flag') : null;
        const updateFlag = function() {
            const selected = select.selectedOptions[0];
            if (!flag || !selected) return;
            const countryName = selected.dataset.country || selected.textContent.trim();
            const iso = selected.dataset.iso || '';
            if (selected.dataset.flag) {
                flag.src = selected.dataset.flag;
            }
            flag.alt = iso ? `${countryName} flag` : 'Country flag';
        };

        select.addEventListener('change', updateFlag);
        updateFlag();
    });

    // Auto-dismiss flash messages after 5 seconds
    const alerts = document.querySelectorAll('.alert-dismissible');
    alerts.forEach(function(alert) {
        setTimeout(function() {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });

    // Confirm delete actions
    document.querySelectorAll('[data-confirm]').forEach(function(el) {
        el.addEventListener('click', function(e) {
            if (!confirm(this.dataset.confirm || 'Are you sure?')) {
                e.preventDefault();
            }
        });
    });

    // Quantity input validation
    document.querySelectorAll('input[type="number"][name="quantity"]').forEach(function(input) {
        input.addEventListener('change', function() {
            const min = parseInt(this.getAttribute('min')) || 1;
            const max = parseInt(this.getAttribute('max')) || 999;
            let val = parseInt(this.value) || 1;
            if (val < min) val = min;
            if (val > max) val = max;
            this.value = val;
        });
    });

    // Slug auto-generation
    const nameInput = document.querySelector('[name="name"]');
    const slugInput = document.querySelector('[name="slug"]');
    if (nameInput && slugInput && !slugInput.value) {
        nameInput.addEventListener('blur', function() {
            if (!slugInput.value) {
                slugInput.value = this.value
                    .toLowerCase()
                    .replace(/[^a-z0-9]+/g, '-')
                    .replace(/^-|-$/g, '');
            }
        });
    }

    // Checkout form validation
    const checkoutForm = document.getElementById('checkout-form');
    if (checkoutForm) {
        checkoutForm.addEventListener('submit', function(e) {
            const phone = document.getElementById('phone');
            const code = checkoutForm.querySelector('[name="phone_country_code"]');
            const fullPhone = document.getElementById('phone_full');
            const local = phone.value.trim().replace(/[^0-9]/g, '').replace(/^0+/, '');
            const phoneVal = `${(code?.value || '+254').replace('+', '')}${local}`;
            const phoneRegex = /^\d{10,15}$/;
            if (fullPhone) fullPhone.value = phoneVal;

            if (!phoneRegex.test(phoneVal)) {
                e.preventDefault();
                alert('Please enter a valid phone number with country code.');
                phone.focus();
                return;
            }

            const agree = document.getElementById('agree');
            if (!agree.checked) {
                e.preventDefault();
                alert('Please agree to the Terms & Conditions to proceed.');
                return;
            }

            // Show loading state
            const btn = document.getElementById('pay-button');
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span> Processing Payment...';
        });
    }

    // Phone number formatting
    const phoneInput = document.getElementById('phone');
    if (phoneInput) {
        phoneInput.addEventListener('input', function() {
            this.value = this.value.replace(/[^0-9]/g, '');
            if (this.value.length > 14) {
                this.value = this.value.slice(0, 14);
            }
        });
    }

    // Cart quantity update
    document.querySelectorAll('.cart-quantity-update').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            // Form submitted normally
        });
    });

    // Search functionality (if implemented)
    const searchInput = document.querySelector('[name="search"]');
    if (searchInput) {
        searchInput.addEventListener('keyup', debounce(function() {
            this.form.submit();
        }, 500));
    }

    // Debounce helper
    function debounce(func, wait) {
        let timeout;
        return function() {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, arguments), wait);
        };
    }

    // Review star highlight
    document.querySelectorAll('.rating-stars input[type="radio"]').forEach(function(input) {
        input.addEventListener('change', function() {
            const labels = document.querySelectorAll('.rating-stars label');
            labels.forEach(function(label) {
                label.style.color = '#ccc';
            });
            const val = parseInt(this.value);
            for (let i = 6 - val; i <= 5; i++) {
                const label = document.querySelector(`.rating-stars label[for="star${i}"]`);
                if (label) label.style.color = '#ffc107';
            }
        });
    });

    // Admin preview confirmations
    document.querySelectorAll('.admin-danger-action').forEach(function(el) {
        el.addEventListener('click', function(e) {
            const msg = this.dataset.message || 'This action cannot be undone. Continue?';
            if (!confirm(msg)) {
                e.preventDefault();
            }
        });
    });

    console.log('SMARKAFRICA loaded successfully');
});
