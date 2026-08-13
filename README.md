# QR Attendance System - Flask + MySQL

## Included
- Flask backend
- MySQL database
- Admin login
- Employee management
- Rotating daily QR token
- GPS restriction during scan
- Check-in / check-out
- Leave management
- Holiday management
- Attendance calculator
- Today's attendance list
- Audit logs
- Admin reports

## Not fully implemented yet
- Real face recognition matching
- Cloud backup automation
- Email notifications

## Default login
- Username: admin
- Password: admin123

## Setup
1. Create a virtual environment and install requirements:
   ```bash
   pip install -r requirements.txt
   ```
2. Import the schema:
   ```bash
   mysql -u root -p < schema.sql
   ```
3. Update MySQL credentials in environment variables or in `app.py`.
4. Run:
   ```bash
   python app.py
   ```
5. Open:
   ```
   http://127.0.0.1:5000
   ```

## GPS setup
Update these values in environment variables if needed:
- `OFFICE_LAT`
- `OFFICE_LNG`
- `OFFICE_RADIUS_METERS`

## Notes
This project uses a rotating daily QR token derived from employee code + date.
The scanner reads the token and the backend verifies whether it is valid for the current day.
