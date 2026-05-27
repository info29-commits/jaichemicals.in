import os
import re
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from database import get_db_connection, init_db

app = Flask(__name__)
app.secret_key = 'super_secret_company_key'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Ensure schema is up to date (safe to re-run — all statements use IF NOT EXISTS)
init_db()

def create_default_admin():
    conn = get_db_connection()
    admin = conn.execute('SELECT * FROM users WHERE email = ?', ('admin@jaioverseas.com',)).fetchone()
    if not admin:
        hashed_pw = generate_password_hash('admin123')
        conn.execute('INSERT INTO users (name, email, phone, password, role) VALUES (?, ?, ?, ?, ?)',
                     ('Admin', 'admin@jaioverseas.com', '0000000000', hashed_pw, 'ADMIN'))
        conn.commit()
    conn.close()

create_default_admin()


# --- AUTH ROUTES ---

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name'].strip()
        email = request.form['email'].strip().lower()
        phone = request.form['phone'].strip()
        password = request.form['password']

        email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_regex, email):
            flash('Please enter a valid email address.', 'error')
            return render_template('signup.html')

        disposable_domains = ['tempmail.com', 'throwaway.email', 'guerrillamail.com', 'mailinator.com', 'yopmail.com']
        domain = email.split('@')[1] if '@' in email else ''
        if domain in disposable_domains:
            flash('Disposable email addresses are not allowed.', 'error')
            return render_template('signup.html')

        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'error')
            return render_template('signup.html')
        if not re.search(r'[A-Za-z]', password):
            flash('Password must contain at least one letter.', 'error')
            return render_template('signup.html')
        if not re.search(r'[0-9]', password):
            flash('Password must contain at least one number.', 'error')
            return render_template('signup.html')
        if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]', password):
            flash('Password must contain at least one special character.', 'error')
            return render_template('signup.html')

        if not name or len(name) < 2:
            flash('Please enter your full name.', 'error')
            return render_template('signup.html')

        phone_clean = re.sub(r'[\s\-\+]', '', phone)
        if not phone_clean.isdigit() or len(phone_clean) < 10:
            flash('Please enter a valid phone number (at least 10 digits).', 'error')
            return render_template('signup.html')

        hashed_pw = generate_password_hash(password)
        conn = get_db_connection()
        try:
            conn.execute('INSERT INTO users (name, email, phone, password) VALUES (?, ?, ?, ?)',
                         (name, email, phone, hashed_pw))
            conn.commit()
            flash('Account created successfully! Please login.', 'success')
            next_url = request.args.get('next')
            if next_url:
                return redirect(url_for('login', next=next_url))
            return redirect(url_for('login'))
        except Exception:
            flash('Email already registered.', 'error')
        finally:
            conn.close()
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']

        email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_regex, email):
            flash('Please enter a valid email address.', 'error')
            return render_template('login.html')

        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        conn.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['user_role'] = user['role']

            if user['role'] == 'ADMIN':
                return redirect(url_for('admin_dashboard'))
            next_url = request.args.get('next')
            if next_url:
                return redirect(next_url)
            return redirect(url_for('index'))
        else:
            flash('Invalid email or password.', 'error')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))


# --- PUBLIC ROUTES ---

@app.route('/')
def index():
    conn = get_db_connection()
    featured_products = conn.execute('SELECT * FROM products ORDER BY id DESC LIMIT 6').fetchall()
    conn.close()
    return render_template('index.html', featured_products=featured_products)

@app.route('/products')
def products():
    conn = get_db_connection()
    categories = conn.execute('SELECT DISTINCT category FROM products').fetchall()
    all_products = conn.execute('SELECT * FROM products').fetchall()
    conn.close()
    return render_template('products.html', products=all_products,
                           categories=[c['category'] for c in categories if c['category']])

@app.route('/product/<int:product_id>')
def product_detail(product_id):
    conn = get_db_connection()
    product = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
    reviews = conn.execute('''
        SELECT pr.*, u.name
        FROM product_reviews pr
        JOIN users u ON pr.user_id = u.id
        WHERE pr.product_id = ?
        ORDER BY pr.created_at DESC
    ''', (product_id,)).fetchall()
    conn.close()
    if not product:
        flash('Product not found.', 'error')
        return redirect(url_for('products'))
    return render_template('product_detail.html', product=product, reviews=reviews)


# --- WISHLIST (MY PRODUCTS) ROUTES ---

@app.route('/add-to-wishlist', methods=['POST'])
def add_to_wishlist():
    if 'user_id' not in session:
        flash('Please sign up or login to add products and connect with our sales team.', 'error')
        return redirect(url_for('signup', next=url_for('products')))

    product_id = request.form['product_id']
    quantity = float(request.form['quantity'])
    unit = request.form['unit']

    conn = get_db_connection()
    product = conn.execute('SELECT name FROM products WHERE id = ?', (product_id,)).fetchone()
    if not product:
        conn.close()
        return redirect(url_for('products'))

    existing = conn.execute(
        'SELECT id, quantity FROM wishlist WHERE user_id = ? AND product_id = ? AND unit = ?',
        (session['user_id'], product_id, unit)
    ).fetchone()

    if existing:
        conn.execute('UPDATE wishlist SET quantity = ? WHERE id = ?',
                     (existing['quantity'] + quantity, existing['id']))
    else:
        conn.execute('INSERT INTO wishlist (user_id, product_id, quantity, unit) VALUES (?, ?, ?, ?)',
                     (session['user_id'], product_id, quantity, unit))

    conn.commit()
    product_name = product['name']
    conn.close()

    flash(f'{product_name} added to My Products.', 'success')
    return redirect(url_for('my_products'))

@app.route('/my-products')
def my_products():
    if 'user_id' not in session:
        flash('Please login to view My Products.', 'error')
        return redirect(url_for('login'))

    conn = get_db_connection()
    items = conn.execute('''
        SELECT w.id, w.quantity, w.unit, w.added_at,
               p.id as product_id, p.name, p.image_url, p.category
        FROM wishlist w
        JOIN products p ON w.product_id = p.id
        WHERE w.user_id = ?
        ORDER BY w.added_at DESC
    ''', (session['user_id'],)).fetchall()
    conn.close()

    return render_template('my_products.html', items=items)

@app.route('/remove-from-wishlist/<int:item_id>', methods=['POST'])
def remove_from_wishlist(item_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    conn.execute('DELETE FROM wishlist WHERE id = ? AND user_id = ?', (item_id, session['user_id']))
    conn.commit()
    conn.close()

    flash('Item removed from My Products.', 'success')
    return redirect(url_for('my_products'))


# --- MY ORDERS ROUTE ---

@app.route('/my-orders')
def my_orders():
    if 'user_id' not in session:
        flash('Please login to view your orders.', 'error')
        return redirect(url_for('login'))

    conn = get_db_connection()
    orders_raw = conn.execute('''
        SELECT id, status, created_at
        FROM orders
        WHERE user_id = ?
        ORDER BY created_at DESC
    ''', (session['user_id'],)).fetchall()

    orders = []
    for o in orders_raw:
        items = conn.execute('''
            SELECT oi.id, oi.quantity, oi.unit, oi.product_id, p.name, p.image_url
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = ?
        ''', (o['id'],)).fetchall()

        order_dict = dict(o)
        order_dict['items'] = items
        orders.append(order_dict)

    conn.close()
    return render_template('my_orders.html', orders=orders)

@app.route('/rate-product', methods=['POST'])
def rate_product():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    product_id = request.form['product_id']
    rating = request.form['rating']
    review_text = request.form.get('review_text', '')

    conn = get_db_connection()
    conn.execute('INSERT INTO product_reviews (user_id, product_id, rating, review_text) VALUES (?, ?, ?, ?)',
                 (session['user_id'], product_id, rating, review_text))
    conn.commit()
    conn.close()

    flash('Thank you for your review!', 'success')
    return redirect(url_for('my_orders'))

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        form_type = request.form.get('form_type')
        conn = get_db_connection()

        if form_type == 'query':
            name = request.form['name']
            contact_info = request.form['contact']
            business = request.form['business']
            product_service = request.form['product_service']
            conn.execute('INSERT INTO queries (name, contact, business, product_service) VALUES (?, ?, ?, ?)',
                         (name, contact_info, business, product_service))
            flash('Your query has been submitted successfully.', 'success')

        elif form_type == 'company_review':
            if 'user_id' not in session:
                flash('Please login to review the company.', 'error')
                return redirect(url_for('login'))
            rating = request.form['rating']
            review_text = request.form.get('review_text', '')
            conn.execute('INSERT INTO company_reviews (user_id, rating, review_text) VALUES (?, ?, ?)',
                         (session['user_id'], rating, review_text))
            flash('Thank you for reviewing Jai Overseas Chem!', 'success')

        conn.commit()
        conn.close()
        return redirect(url_for('contact'))

    conn = get_db_connection()
    reviews = conn.execute('''
        SELECT cr.*, u.name
        FROM company_reviews cr
        JOIN users u ON cr.user_id = u.id
        ORDER BY cr.created_at DESC
    ''').fetchall()
    conn.close()
    return render_template('contact_reviews.html', reviews=reviews)


# --- ADMIN ROUTES ---

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('user_role') != 'ADMIN':
            flash('Admin access required.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin')
@admin_required
def admin_dashboard():
    conn = get_db_connection()
    user_count = conn.execute('SELECT COUNT(*) FROM users WHERE role="USER"').fetchone()[0]
    product_count = conn.execute('SELECT COUNT(*) FROM products').fetchone()[0]
    order_count = conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
    queries_count = conn.execute('SELECT COUNT(*) FROM queries').fetchone()[0]
    wishlist_users = conn.execute('SELECT COUNT(DISTINCT user_id) FROM wishlist').fetchone()[0]
    conn.close()
    return render_template('admin_dashboard.html', users=user_count, products=product_count,
                           orders=order_count, queries=queries_count, wishlist_users=wishlist_users)

@app.route('/admin/products', methods=['GET', 'POST'])
@admin_required
def admin_products():
    conn = get_db_connection()
    if request.method == 'POST':
        name = request.form['name']
        category = request.form['category']
        description = request.form['description']
        specification = request.form['specification']
        application = request.form['application']

        image_file = request.files.get('image')
        image_url = None
        if image_file and image_file.filename != '':
            filename = secure_filename(image_file.filename)
            image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            image_url = f"/static/uploads/{filename}"

        video_file = request.files.get('video')
        video_url = None
        if video_file and video_file.filename != '':
            filename = secure_filename(video_file.filename)
            video_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            video_url = f"/static/uploads/{filename}"

        conn.execute('''
            INSERT INTO products (name, category, description, specification, application, price, image_url, video_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, category, description, specification, application, 0, image_url, video_url))
        conn.commit()
        flash('Product added successfully.', 'success')
        return redirect(url_for('admin_products'))

    products = conn.execute('SELECT * FROM products').fetchall()
    conn.close()
    return render_template('admin_products.html', products=products)

@app.route('/admin/products/edit/<int:product_id>', methods=['POST'])
@admin_required
def admin_product_edit(product_id):
    name = request.form['name']
    category = request.form.get('category', '')
    description = request.form.get('description', '')
    specification = request.form.get('specification', '')
    application = request.form.get('application', '')

    image_url = request.form.get('existing_image_url') or None
    image_file = request.files.get('image')
    if image_file and image_file.filename != '':
        filename = secure_filename(image_file.filename)
        image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        image_url = f"/static/uploads/{filename}"

    video_url = request.form.get('existing_video_url') or None
    video_file = request.files.get('video')
    if video_file and video_file.filename != '':
        filename = secure_filename(video_file.filename)
        video_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        video_url = f"/static/uploads/{filename}"

    conn = get_db_connection()
    conn.execute('''
        UPDATE products
        SET name=?, category=?, description=?, specification=?, application=?, image_url=?, video_url=?
        WHERE id=?
    ''', (name, category, description, specification, application, image_url, video_url, product_id))
    conn.commit()
    conn.close()

    flash('Product updated successfully.', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/products/delete/<int:product_id>', methods=['POST'])
@admin_required
def admin_product_delete(product_id):
    conn = get_db_connection()
    order_count = conn.execute(
        'SELECT COUNT(*) FROM order_items WHERE product_id=?', (product_id,)
    ).fetchone()[0]
    if order_count > 0:
        flash(f'Cannot delete: this product is linked to {order_count} order item(s).', 'error')
        conn.close()
        return redirect(url_for('admin_products'))
    conn.execute('DELETE FROM product_reviews WHERE product_id=?', (product_id,))
    conn.execute('DELETE FROM wishlist WHERE product_id=?', (product_id,))
    conn.execute('DELETE FROM products WHERE id=?', (product_id,))
    conn.commit()
    conn.close()
    flash('Product deleted successfully.', 'success')
    return redirect(url_for('admin_products'))


# --- ADMIN: USER PRODUCTS (WISHLIST) ---

@app.route('/admin/user-products')
@admin_required
def admin_user_products():
    conn = get_db_connection()
    users = conn.execute('SELECT id, name, email, phone FROM users WHERE role="USER"').fetchall()

    user_wishlists = []
    for user in users:
        items = conn.execute('''
            SELECT w.id, w.quantity, w.unit, w.added_at,
                   p.id as product_id, p.name, p.image_url, p.category
            FROM wishlist w
            JOIN products p ON w.product_id = p.id
            WHERE w.user_id = ?
            ORDER BY w.added_at DESC
        ''', (user['id'],)).fetchall()
        user_wishlists.append({
            'user': dict(user),
            'items': [dict(i) for i in items]
        })

    conn.close()
    return render_template('admin_user_products.html', user_wishlists=user_wishlists)

@app.route('/admin/wishlist/update/<int:item_id>', methods=['POST'])
@admin_required
def admin_update_wishlist_item(item_id):
    quantity = float(request.form['quantity'])
    unit = request.form['unit']

    conn = get_db_connection()
    conn.execute('UPDATE wishlist SET quantity = ?, unit = ? WHERE id = ?', (quantity, unit, item_id))
    conn.commit()
    conn.close()

    flash('Wishlist item updated.', 'success')
    return redirect(url_for('admin_user_products'))

@app.route('/admin/wishlist/delete/<int:item_id>', methods=['POST'])
@admin_required
def admin_delete_wishlist_item(item_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM wishlist WHERE id = ?', (item_id,))
    conn.commit()
    conn.close()

    flash('Item removed from user wishlist.', 'success')
    return redirect(url_for('admin_user_products'))

@app.route('/admin/create-order/<int:user_id>', methods=['POST'])
@admin_required
def admin_create_order(user_id):
    conn = get_db_connection()
    wishlist_items = conn.execute('''
        SELECT w.id, w.product_id, w.quantity, w.unit, p.name
        FROM wishlist w
        JOIN products p ON w.product_id = p.id
        WHERE w.user_id = ?
    ''', (user_id,)).fetchall()

    if not wishlist_items:
        flash('No items in user wishlist to confirm.', 'error')
        conn.close()
        return redirect(url_for('admin_user_products'))

    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO orders (user_id, total_amount, payment_status, status) VALUES (?, ?, ?, ?)',
        (user_id, 0, 'Confirmed', 'Order Received')
    )
    order_id = cursor.lastrowid

    for item in wishlist_items:
        cursor.execute(
            'INSERT INTO order_items (order_id, product_id, quantity, unit, price) VALUES (?, ?, ?, ?, ?)',
            (order_id, item['product_id'], item['quantity'], item['unit'], 0)
        )

    conn.execute('DELETE FROM wishlist WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

    flash('Order confirmed and created for user.', 'success')
    return redirect(url_for('admin_orders'))


# --- ADMIN: ORDERS (CONFIRMED) ---

@app.route('/admin/orders', methods=['GET', 'POST'])
@admin_required
def admin_orders():
    conn = get_db_connection()
    if request.method == 'POST':
        order_id = request.form['order_id']
        status = request.form['status']
        conn.execute('UPDATE orders SET status=? WHERE id=?', (status, order_id))
        conn.commit()
        flash('Order status updated.', 'success')
        return redirect(url_for('admin_orders'))

    orders_raw = conn.execute('''
        SELECT o.id, u.name as user_name, u.email, u.phone, o.status, o.created_at
        FROM orders o
        JOIN users u ON o.user_id = u.id
        ORDER BY o.created_at DESC
    ''').fetchall()

    orders = []
    for o in orders_raw:
        items = conn.execute('''
            SELECT oi.id, oi.quantity, oi.unit, oi.product_id, p.name, p.image_url
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = ?
        ''', (o['id'],)).fetchall()

        order_dict = dict(o)
        order_dict['items'] = items
        orders.append(order_dict)

    conn.close()
    return render_template('admin_orders.html', orders=orders)

@app.route('/admin/orders/update-item/<int:item_id>', methods=['POST'])
@admin_required
def admin_update_order_item(item_id):
    quantity = float(request.form['quantity'])
    unit = request.form['unit']
    order_id = request.form['order_id']

    conn = get_db_connection()
    conn.execute('UPDATE order_items SET quantity = ?, unit = ? WHERE id = ?', (quantity, unit, item_id))
    conn.commit()
    conn.close()

    flash('Order item updated.', 'success')
    return redirect(url_for('admin_orders'))

@app.route('/admin/orders/delete-item/<int:item_id>', methods=['POST'])
@admin_required
def admin_delete_order_item(item_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM order_items WHERE id = ?', (item_id,))
    conn.commit()
    conn.close()

    flash('Order item removed.', 'success')
    return redirect(url_for('admin_orders'))


@app.route('/admin/users')
@admin_required
def admin_users():
    conn = get_db_connection()
    users = conn.execute('SELECT * FROM users WHERE role="USER"').fetchall()
    conn.close()
    return render_template('admin_users.html', users=users)

@app.route('/admin/queries')
@admin_required
def admin_queries():
    conn = get_db_connection()
    queries = conn.execute('SELECT * FROM queries ORDER BY created_at DESC').fetchall()
    conn.close()
    return render_template('admin_queries.html', queries=queries)


if __name__ == '__main__':
    app.run(debug=True, port=5000)
