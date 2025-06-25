import os
import uuid
import time
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests # Used for making HTTP requests to Pesapal

# --- Basic Setup ---
app = Flask(__name__)
# Allow requests from your frontend's domain in a production environment
# For development, "*" is okay. For production, be more specific.
# e.g., CORS(app, resources={r"/api/*": {"origins": "https://www.yourdomain.com"}})
CORS(app, resources={r"/api/*": {"origins": "*"}})

# --- Pesapal Configuration ---
# ======================== IMPORTANT ========================
# REPLACE THESE WITH YOUR ACTUAL LIVE/SANDBOX CREDENTIALS FROM PESAPAL
PESAPAL_CONSUMER_KEY = os.environ.get("PESAPAL_CONSUMER_KEY", "ngW+UEcnDhltUc5fxPfrCD987xMh3Lx8")
PESAPAL_CONSUMER_SECRET = os.environ.get("PESAPAL_CONSUMER_SECRET", "q27RChYs5UkypdcNYKzuUw460Dg=")

# ======================== IMPORTANT ========================
# REPLACE THIS WITH YOUR PUBLICLY ACCESSIBLE BACKEND DOMAIN
# This MUST be an https:// URL.
YOUR_PUBLIC_DOMAIN = "https://your-live-backend-app.herokuapp.com" 

# Use the sandbox URL for testing and the live URL for production
PESAPAL_API_DOMAIN = "https://cybqa.pesapal.com" # Sandbox/Test URL
# PESAPAL_API_DOMAIN = "https://pay.pesapal.com" # Live/Production URL

# This is a temporary, in-memory "database" to track transactions.
# In a real production application, you MUST use a persistent database like PostgreSQL, MySQL, or Firestore.
transactions_db = {}

# --- Helper Functions ---

def get_pesapal_token():
    """
    Authenticates with Pesapal to get an OAuth2 bearer token.
    This token is required for all subsequent API calls.
    """
    url = f"{PESAPAL_API_DOMAIN}/v3/api/Auth/RequestToken"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    payload = {"consumer_key": PESAPAL_CONSUMER_KEY, "consumer_secret": PESAPAL_CONSUMER_SECRET}
    
    # This is the real API call. It will fail if your credentials are placeholders.
    try:
        if PESAPAL_CONSUMER_KEY == "YOUR_PESAPAL_CONSUMER_KEY":
            print("WARNING: Using placeholder Pesapal credentials. API calls will fail.")
            return None
        
        print(f"Requesting token from {url}")
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status() # Raises an HTTPError for bad responses (4xx or 5xx)
        token_data = response.json()
        print("Successfully obtained Pesapal token.")
        return token_data.get("token")
    except requests.exceptions.RequestException as e:
        print(f"ERROR [get_pesapal_token]: Failed to connect to Pesapal. {e}")
        return None

def register_ipn_url(token):
    """
    Registers the IPN (Instant Payment Notification) URL with Pesapal.
    This is the URL Pesapal will send transaction updates to.
    """
    url = f"{PESAPAL_API_DOMAIN}/v3/api/URLSetup/RegisterIPN"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"}
    
    # This URL MUST be a publicly accessible HTTPS endpoint.
    ipn_callback_url = f"{YOUR_PUBLIC_DOMAIN}/api/pesapal-ipn-callback"
    payload = {"url": ipn_callback_url, "ipn_notification_type": "GET"}

    try:
        if "your-live-backend-app" in YOUR_PUBLIC_DOMAIN:
            print(f"WARNING: Using placeholder domain '{YOUR_PUBLIC_DOMAIN}'. IPN registration will likely fail or point to a non-existent URL.")
            return None

        print(f"Registering IPN URL: {ipn_callback_url}")
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        ipn_data = response.json()
        print(f"Successfully registered IPN URL. IPN ID: {ipn_data.get('ipn_id')}")
        return ipn_data.get('ipn_id')
    except requests.exceptions.RequestException as e:
        print(f"ERROR [register_ipn_url]: {e}")
        return None

def submit_order_request(token, ipn_id, amount, phone, description, tracking_id):
    """
    Submits the actual payment order to Pesapal. This is what triggers the STK Push.
    """
    url = f"{PESAPAL_API_DOMAIN}/v3/api/Transactions/SubmitOrderRequest"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"}
    
    # This is the URL the user is redirected to from your site if needed (not common for STK push).
    # Can be your frontend's homepage or a "thank you" page.
    callback_url = "https://www.google.com/" # Replace with your frontend URL

    payload = {
        "language": "EN",
        "currency": "TZS",
        "amount": amount,
        "description": description,
        "callback_url": callback_url,
        "notification_id": ipn_id,
        "id": tracking_id, # Your unique internal tracking ID
        "billing_address": {
            "phone_number": phone
        }
    }
    
    try:
        print(f"Submitting Order Request for Tracking ID: {tracking_id}")
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        order_data = response.json()
        print(f"Successfully submitted order. Pesapal Response: {order_data}")
        return order_data
    except requests.exceptions.RequestException as e:
        print(f"ERROR [submit_order_request]: {e}")
        return {"error": str(e), "status": "500"}

def get_transaction_status(token, order_tracking_id):
    """Helper to get the latest status from Pesapal."""
    url = f"{PESAPAL_API_DOMAIN}/v3/api/Transactions/GetTransactionStatus?orderTrackingId={order_tracking_id}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"ERROR [get_transaction_status]: {e}")
        return None

# --- API Endpoints ---

@app.route('/api/make-payment', methods=['POST'])
def make_payment():
    data = request.get_json()
    amount = data.get('amount')
    phone = data.get('phone')
    
    if not all([amount, phone]):
        return jsonify({"error": "Amount and Phone Number are required"}), 400
    
    tracking_id = str(uuid.uuid4())
    description = f"Payment for order {tracking_id[:8]}"
    
    print(f"\n--- [LIVE] New Payment Request --- | Amount: {amount} | Phone: {phone} | ID: {tracking_id}")
    
    # STEP 1: Get Authentication Token
    token = get_pesapal_token()
    if not token:
        return jsonify({"error": "Failed to authenticate with payment provider. Check your credentials."}), 500
        
    # STEP 2: Register IPN URL
    # In a real app, you might do this once and store the ipn_id.
    ipn_id = register_ipn_url(token)
    if not ipn_id:
        return jsonify({"error": "Failed to register notification URL. Check your public domain configuration."}), 500

    # STEP 3: Submit Order to trigger STK Push
    order_response = submit_order_request(token, ipn_id, amount, phone, description, tracking_id)
    
    if order_response.get("error"):
        return jsonify({"error": f"Payment initiation failed: {order_response.get('error')}"}), 500

    # Store the transaction in our local "database" with a 'Pending' status
    transactions_db[tracking_id] = {
        "status": "Pending",
        "pesapal_tracking_id": order_response.get("order_tracking_id"),
        "amount": amount,
        "phone": phone,
        "time_created": time.time()
    }

    # Respond to the frontend. The frontend will now poll for status updates.
    return jsonify({
        "message": "Transaction initiated. Please check your phone to enter PIN.",
        "order_tracking_id": tracking_id # Use our internal ID for polling
    })

@app.route('/api/check-status/<tracking_id>', methods=['GET'])
def check_status(tracking_id):
    """
    Endpoint for the frontend to poll for transaction status.
    This is a fallback/secondary confirmation method. The IPN is the primary method.
    """
    transaction = transactions_db.get(tracking_id)
    if not transaction:
        return jsonify({"payment_status": "Invalid"}), 404
        
    # Simply return the current status from our database.
    # The database status is updated by the IPN callback.
    print(f"Polling request for {tracking_id}. Current status: {transaction['status']}")
    return jsonify({"payment_status": transaction["status"]})

@app.route('/api/pesapal-ipn-callback', methods=['GET'])
def ipn_callback():
    """
    THIS IS THE MOST IMPORTANT ENDPOINT FOR LIVE TRANSACTIONS.
    Pesapal calls this URL to notify our server about status changes.
    This endpoint MUST be publicly accessible on the internet.
    """
    print("\n--- IPN CALLBACK RECEIVED ---")
    
    # Pesapal sends notification details in the query string for GET requests
    order_tracking_id = request.args.get('OrderTrackingId')
    merchant_reference = request.args.get('OrderMerchantReference') # This is our internal tracking_id

    print(f"IPN for our ID: {merchant_reference} | Pesapal's ID: {order_tracking_id}")
    
    if not all([order_tracking_id, merchant_reference]):
        print("IPN call missing required parameters.")
        return "IPN Error: Missing parameters", 400

    # Security check: Does this transaction exist in our database?
    if merchant_reference not in transactions_db:
        print(f"IPN received for an unknown transaction: {merchant_reference}")
        return "IPN Error: Unknown transaction", 404
        
    # As a best practice, get a fresh auth token and query the transaction status
    # to verify the IPN is legitimate and get the final status.
    token = get_pesapal_token()
    if token:
        status_details = get_transaction_status(token, order_tracking_id)
        if status_details:
            payment_status = status_details.get("status_code_description") # e.g., 'Completed', 'Failed', 'Cancelled'
            print(f"Verified status from Pesapal API: '{payment_status}'")
            # Update our database with the confirmed status
            transactions_db[merchant_reference]['status'] = payment_status
        else:
            print("Could not verify transaction status from Pesapal API after IPN.")
    else:
        print("Could not get token to verify IPN.")

    # Acknowledge receipt to Pesapal. The body must be exactly this format.
    response_body = f"pesapal_notification_id={order_tracking_id}&pesapal_tracking_id={order_tracking_id}&pesapal_merchant_reference={merchant_reference}"
    return response_body, 200

# --- How to Run ---
# 1. Install dependencies: pip install Flask Flask-Cors requests
# 2. Set your credentials and public domain in the script.
# 3. Deploy to a public server.
# 4. Run the server (command will depend on your hosting provider, e.g., gunicorn app:app).
if __name__ == '__main__':
    # This is for local development testing only.
    # Use a production-grade WSGI server like Gunicorn or uWSGI for deployment.
    app.run(port=5000, debug=True)
