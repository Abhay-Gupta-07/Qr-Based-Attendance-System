CREATE DATABASE IF NOT EXISTS qr_attendance_system;
USE qr_attendance_system;

CREATE TABLE IF NOT EXISTS admins (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(50) UNIQUE NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  full_name VARCHAR(100) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS employees (
  id INT AUTO_INCREMENT PRIMARY KEY,
  employee_code VARCHAR(30) UNIQUE NOT NULL,
  full_name VARCHAR(100) NOT NULL,
  department VARCHAR(100) NOT NULL,
  designation VARCHAR(100) NOT NULL,
  gmail VARCHAR(100),
  password_hash VARCHAR(255),
  active TINYINT DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS attendance (
  id INT AUTO_INCREMENT PRIMARY KEY,
  employee_id INT NOT NULL,
  attendance_date DATE NOT NULL,
  check_in_time TIME NULL,
  check_out_time TIME NULL,
  status VARCHAR(20) DEFAULT 'PRESENT',
  gps_lat DECIMAL(10,7) NULL,
  gps_lng DECIMAL(10,7) NULL,
  face_verified TINYINT DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_employee_date (employee_id, attendance_date),
  CONSTRAINT fk_att_employee FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS leave_requests (
  id INT AUTO_INCREMENT PRIMARY KEY,
  employee_id INT NOT NULL,
  from_date DATE NOT NULL,
  to_date DATE NOT NULL,
  reason VARCHAR(255),
  status VARCHAR(20) DEFAULT 'APPROVED',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_leave_employee FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS holidays (
  id INT AUTO_INCREMENT PRIMARY KEY,
  holiday_date DATE UNIQUE NOT NULL,
  name VARCHAR(120) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  admin_id INT NULL,
  action VARCHAR(80) NOT NULL,
  details TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_audit_admin FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE SET NULL
);

-- Insert All Holidays for 2026
INSERT IGNORE INTO holidays (holiday_date, name) VALUES
('2026-01-01', 'New Year Day'),
('2026-01-26', 'Republic Day'),
('2026-02-01', 'Basant Panchami'),
('2026-03-08', 'Maha Shivaratri'),
('2026-03-17', 'Holi'),
('2026-03-25', 'Holi Holiday'),
('2026-03-29', 'Good Friday'),
('2026-04-02', 'Ram Navami'),
('2026-04-10', 'Good Friday Holiday'),
('2026-04-14', 'Dr. Ambedkar Jayanti'),
('2026-04-21', 'Mahavir Jayanti'),
('2026-05-01', 'May Day'),
('2026-05-15', 'Buddha Purnima'),
('2026-05-20', 'Eid ul-Fitr'),
('2026-07-07', 'Eid ul-Adha'),
('2026-07-17', 'Muharram'),
('2026-08-15', 'Independence Day'),
('2026-08-22', 'Janmashtami'),
('2026-08-31', 'Janmashtami Holiday'),
('2026-09-02', 'Ganesh Chaturthi'),
('2026-09-16', 'Milad un-Nabi'),
('2026-10-02', 'Gandhi Jayanti'),
('2026-10-05', 'Dussehra'),
('2026-10-13', 'Dussehra Holiday'),
('2026-10-24', 'Diwali'),
('2026-10-25', 'Diwali Day 2'),
('2026-10-26', 'Diwali Holiday'),
('2026-11-01', 'Diwali Holiday 2'),
('2026-11-08', 'Guru Nanak Jayanti'),
('2026-11-09', 'Guru Nanak Holiday'),
('2026-12-25', 'Christmas'),
('2026-12-26', 'Christmas Holiday');

