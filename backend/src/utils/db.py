import os
from datetime import datetime
import bcrypt
import urllib.parse
import json
import psycopg2
import logging
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import traceback

load_dotenv()

class PGDB:
    def __init__(self):
        # host = os.getenv('HOST')
        # port = os.getenv('DB_PORT')
        # user = os.getenv('USER')
        # password = os.getenv('PASSWORD')
        # db_name = os.getenv('DATABASE_NAME')

        # if not all([host, port, user, password, db_name]):
        #     raise ValueError("Missing required database environment variables")

        # password_encoded = urllib.parse.quote_plus(password)
        # self.connection_string = f"postgresql://{user}:{password_encoded}@{host}:{port}/{db_name}"
        self.connection_string = os.getenv('DATABASE_URL')

        self.create_users_table()
        self.create_call_history_table()
        self.create_appointments_table()
    def get_connection(self):
        try:
            return psycopg2.connect(self.connection_string)
        except Exception as e:
            logging.error(f"Database connection error: {e}")
            raise

    def create_users_table(self):
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        username VARCHAR(100),
                        email VARCHAR(100) UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        first_name VARCHAR(100),
                        last_name VARCHAR(100),
                        is_admin BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                """)
            conn.commit()
        except Exception as e:
            logging.error(f"Error creating users table: {e}")
        finally:
            conn.close()

    
    def create_call_history_table(self):
        """
        Create call_history table to store call details
        """
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS call_history (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        call_id TEXT NOT NULL UNIQUE,
                        status TEXT,
                        duration DOUBLE PRECISION,  
                        transcript JSONB,
                        summary TEXT,
                        recording_url TEXT,
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                        started_at TIMESTAMPTZ NULL,
                        ended_at TIMESTAMPTZ NULL,
                        voice_id TEXT,
                        voice_name TEXT,
                        from_number TEXT NULL,
                        to_number TEXT NULL,
                        transcript_url TEXT,     -- ADDED
                        transcript_blob TEXT,    -- ADDED
                        recording_blob TEXT      -- ADDED
                    );
                """)
            conn.commit()
        except Exception as e:
            logging.error(f"Error creating call_history table: {e}")
        finally:
            conn.close()


    # ============================= USERS LOGIC START =============================


    def register_user(self, user_data):
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # Check if email already exists
                cursor.execute("SELECT id FROM users WHERE email = %s", (user_data['email'],))
                if cursor.fetchone():
                    raise ValueError("Email already registered.")

                # Hash the password
                hashed_password = bcrypt.hashpw(user_data['password'].encode('utf-8'), bcrypt.gensalt())

                # Insert user
                cursor.execute("""
                    INSERT INTO users (username, email, password_hash,is_admin)
                    VALUES (%s, %s, %s,%s)
                    RETURNING id, username, email, created_at,is_admin;
                """, (
                    user_data['username'],
                    user_data['email'],
                    hashed_password.decode('utf-8'),
                    user_data['is_admin']
                ))

                row = cursor.fetchone()
                conn.commit()

                # Match output with UserOut model
                return {
                    "id": row["id"],
                    "username": row["username"],
                    "email": row["email"],
                    "created_at": row["created_at"]
                }

        except Exception as e:
            conn.rollback()
            logging.error(f"Error in register_user: {e}")
            raise
        finally:
            conn.close()

    def login_user(self, user_data):
        """Verify user credentials by username or email and return user info."""
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT id, username, email, password_hash,first_name,last_name,created_at,is_admin
                    FROM users
                    WHERE username = %s OR email = %s
                    LIMIT 1
                """, (user_data.get("username"), user_data['email']))

                result = cursor.fetchone()

                if result and bcrypt.checkpw(user_data['password'].encode('utf-8'), result[3].encode('utf-8')):
                    return {
                        "id": result[0],
                        "username": result[1],
                        "email": result[2],
                        # "first_name": result[4],
                        # "last_name": result[5],
                        "created_at": result[6],
                        "is_admin": result[7]
                    }
                else:
                    raise ValueError("Invalid username or password.")
        except Exception as e:
            logging.error(f"Error during login: {str(e)}")
            raise
        finally:
            conn.close()


    def get_user_by_id(self, user_id: int):
        """Get user by ID"""
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT id,first_name,last_name,username,email,is_admin,created_at FROM users WHERE id = %s",
                    (user_id,)
                )
                return cursor.fetchone()
        finally:
            if conn:
                conn.close()

    def delete_user_by_id(self,user_id):
        """
        delete user by id
        """
        conn = None 
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM users WHERE id = %s
                    """,
                    (user_id,)     
                )
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"Error deleting user {user_id}: {e}")
            if conn:
                conn.rollback()
                return False
        finally:
            if conn:
                conn.close()

    def update_user_name_fields(self, user_id: int, first_name: str, last_name: str):
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE users
                    SET first_name = %s,
                        last_name = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (first_name, last_name, user_id))
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"Error updating name fields: {e}")
            return False
        finally:
            conn.close()

    def change_user_password(self, user_id: int, current_password: str, new_password: str):
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT password_hash FROM users WHERE id = %s
                """, (user_id,))
                result = cursor.fetchone()
                if not result:
                    raise ValueError("User not found.")

                # Verify current password
                if not bcrypt.checkpw(current_password.encode(), result[0].encode()):
                    raise ValueError("Current password is incorrect.")

                # Hash new password
                new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

                # Update
                cursor.execute("""
                    UPDATE users SET password_hash = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (new_hash, user_id))
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"Password change error: {e}")
            raise
        finally:
            conn.close()             

    def get_all_users(self):
        query = """
            SELECT id, first_name, last_name, username, email, is_admin, created_at
            FROM users
            WHERE is_admin = FALSE
            ORDER BY created_at DESC
        """
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(query)
                result = cursor.fetchall()
                return [
                    {
                        "id": row[0],
                        # "first_name": row[1],
                        # "last_name": row[2],
                        "username": row[3],
                        "email": row[4],
                        "is_admin": row[5],
                        "created_at": row[6],
                    }
                    for row in result
                ]
        finally:
            conn.close()

    def get_all_users_paginated(self, page: int = 1, page_size: int = 10):
        query_total = "SELECT COUNT(*) FROM users WHERE is_admin = FALSE"
        query_data = """
            SELECT id, first_name, last_name, username, email, is_admin, created_at
            FROM users
            -- WHERE is_admin = FALSE
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """

        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                # Total user count
                cursor.execute(query_total)
                total_users = cursor.fetchone()[0]

                # Paginated data
                offset = (page - 1) * page_size
                cursor.execute(query_data, (page_size, offset))
                rows = cursor.fetchall()

                users = [
                    {
                        "id": row[0],
                        # "first_name": row[1],
                        # "last_name": row[2],
                        "username": row[3],
                        "email": row[4],
                        "is_admin": row[5],
                        "created_at": row[6],
                    }
                    for row in rows
                ]

            return {
                "users": users,
                "total": total_users
            }

        except Exception as e:
            print(f"Error fetching paginated users: {e}")
            return {"users": [], "total": 0}
        finally:
            conn.close()             

    def insert_call_history(
        self,
        user_id: int,
        call_id: str,
        status: str = None,
        voice_id: str = None,
        voice_name: str = None,
        to_number: str = None
    ):
        """
        Insert a new call history record with initial data.
        Other fields (transcript, summary, duration, etc.) will be updated later.
        """
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                values = (
                    user_id, call_id, status,
                    voice_id, voice_name, to_number
                )

                cursor.execute("""
                    INSERT INTO call_history (
                        user_id, call_id, status,
                        voice_id, voice_name, to_number
                    )
                    VALUES (%s,%s,%s,%s,%s,%s)
                    RETURNING id;
                """, values)

                row = cursor.fetchone()
                conn.commit()
                return row[0] if row else None

        except Exception as e:
            logging.error(f"Error inserting call history: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()



    def update_call_history(self, call_id: str, updates: dict):
        """
        Update specific fields in the call_history record based on the call_id.

        Args:
            call_id (str): The unique identifier for the call.
            updates (dict): A dictionary where keys are column names and values
                            are the new values to set. e.g., {"status": "completed", "duration": 120.5}
        """
        if not updates:
            logging.warning("update_call_history called with no updates.")
            return None # Or raise an error

        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                # Build the SET part of the SQL query dynamically
                set_clauses = []
                param_values = []
                for key, value in updates.items():
                    
                    # ==========================================================
                    # ⭐️ BUG FIX IS HERE ⭐️
                    # The old logic ('if not key.isalnum() and key != '_'') was
                    # rejecting valid keys like 'transcript_url'.
                    # This new logic allows keys with underscores.
                    # ==========================================================
                    if not key.replace('_', '').isalnum():
                        logging.error(f"Invalid column name detected: {key}")
                        raise ValueError(f"Invalid column name: {key}")

                    # Handle JSON data specifically
                    if key == 'transcript' and value is not None:
                        set_clauses.append(f"{key} = %s")
                        param_values.append(json.dumps(value))
                    else:
                        set_clauses.append(f"{key} = %s")
                        param_values.append(value)

                if not set_clauses:
                    logging.warning("No valid fields to update.")
                    return None

                set_sql = ", ".join(set_clauses)
                sql = f"UPDATE call_history SET {set_sql} WHERE call_id = %s RETURNING id;"
                
                # Add call_id to the parameters list
                param_values.append(call_id)

                logging.debug(f"Executing SQL: {sql} with params: {param_values}") # Optional: Log SQL for debugging

                cursor.execute(sql, tuple(param_values))

                row = cursor.fetchone()
                conn.commit()
                logging.info(f"Updated call_history for call_id {call_id}. Updated fields: {list(updates.keys())}")
                return row[0] if row else None

        except Exception as e:
            conn.rollback()
            logging.error(f"Error updating call history for call_id={call_id}: {e}")
            traceback.print_exc() # Print full traceback
            raise # Re-raise the exception
        finally:
            conn.close()

    # def get_call_history_by_user_id(self, user_id: int, page: int = 1, page_size: int = 10):
    #     """
    #     Fetch paginated call history for a user (with JOIN on users table).
    #     Includes call details, voice info, caller/callee numbers, and timestamps.
    #     """
    #     conn = self.get_connection()
    #     try:
    #         with conn.cursor(cursor_factory=RealDictCursor) as cursor:
    #             # Count total records
    #             cursor.execute("SELECT COUNT(*) FROM call_history WHERE user_id = %s", (user_id,))
    #             total = cursor.fetchone()["count"]

    #             # Paginated query
    #             offset = (page - 1) * page_size
    #             cursor.execute("""
    #                 SELECT ch.id, ch.call_id, ch.status, ch.duration, ch.transcript,
    #                     ch.summary, ch.recording_url, ch.created_at, ch.started_at, ch.ended_at,
    #                     ch.voice_id, ch.voice_name, ch.from_number, ch.to_number,
    #                     u.id AS user_id, u.username, u.email
    #                 FROM call_history ch
    #                 JOIN users u ON ch.user_id = u.id
    #                 WHERE ch.user_id = %s
    #                 ORDER BY ch.created_at DESC
    #                 LIMIT %s OFFSET %s
    #             """, (user_id, page_size, offset))
                
    #             rows = cursor.fetchall()

    #             # Ensure transcript is JSON
    #             for row in rows:
    #                 if isinstance(row["transcript"], str):
    #                     try:
    #                         row["transcript"] = json.loads(row["transcript"])
    #                     except Exception:
    #                         logging.warning(f"Invalid JSON in transcript for call_id={row['call_id']}")

    #             return {"calls": rows, "total": total, "page": page, "page_size": page_size}
    #     except Exception as e:
    #         logging.error(f"Error fetching call history for user_id={user_id}: {e}")
    #         raise
    #     finally:
    #         conn.close()

    def get_call_history_by_user_id(self, user_id: int, page: int = 1, page_size: int = 10):
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # Count total records
                cursor.execute("SELECT COUNT(*) FROM call_history WHERE user_id = %s", (user_id,))
                total = cursor.fetchone()["count"]

                # Count completed
                cursor.execute("""
                    SELECT COUNT(*) FROM call_history 
                    WHERE user_id = %s AND status = 'completed'
                """, (user_id,))
                completed_calls = cursor.fetchone()["count"]

                not_completed_calls = total - completed_calls

                # Paginated query
                offset = (page - 1) * page_size
                cursor.execute("""
                    SELECT ch.id, ch.call_id, ch.status, ch.duration, ch.transcript,
                        ch.summary, ch.recording_url, ch.created_at, ch.started_at, ch.ended_at,
                        ch.voice_id, ch.voice_name, ch.from_number, ch.to_number,
                        u.id AS user_id, u.username, u.email
                    FROM call_history ch
                    JOIN users u ON ch.user_id = u.id
                    WHERE ch.user_id = %s
                    ORDER BY ch.created_at DESC
                    LIMIT %s OFFSET %s
                """, (user_id, page_size, offset))

                rows = cursor.fetchall()

                # Ensure transcript is JSON
                for row in rows:
                    if isinstance(row["transcript"], str):
                        try:
                            row["transcript"] = json.loads(row["transcript"])
                        except Exception:
                            logging.warning(f"Invalid JSON in transcript for call_id={row['call_id']}")

                return {
                    "calls": rows,
                    "total": total,
                    "completed_calls": completed_calls,
                    "not_completed_calls": not_completed_calls,
                    "page": page,
                    "page_size": page_size
                }
        except Exception as e:
            logging.error(f"Error fetching call history for user_id={user_id}: {e}")
            raise
        finally:
            conn.close()


    def create_appointment(
        self,
        user_id: int,
        appointment_date: str,
        start_time: str,
        end_time: str,
        attendee_email: str,
        attendee_name: str,
        title: str,
        description: str = "",
        notes: str = "" 
    ):
        """Create a new appointment"""
        query = """
            INSERT INTO appointments 
            (user_id, appointment_date, start_time, end_time, attendee_email, attendee_name, title, description, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (
                    user_id, appointment_date, start_time, end_time,
                    attendee_email, attendee_name, title, description, notes
                ))
                appointment_id = cursor.fetchone()[0]
                conn.commit()
                return appointment_id
                
    def create_appointments_table(self):
        """
        Create appointments table to store meeting scheduling data
        """
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS appointments (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        appointment_date DATE NOT NULL,
                        start_time TIME NOT NULL,
                        end_time TIME NOT NULL,
                        attendee_email VARCHAR(255) NOT NULL,
                        attendee_name VARCHAR(255),
                        title TEXT NOT NULL,
                        description TEXT,
                        notes TEXT,  -- ✅ NEW FIELD
                        status VARCHAR(50) DEFAULT 'scheduled',
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                """)
            conn.commit()
        except Exception as e:
            logging.error(f"Error creating appointments table: {e}")
        finally:
            conn.close()


    def get_user_appointments(self, user_id: int, from_date: str = None):
        """Get all appointments for a user from a specific date onwards"""
        if from_date is None:
            from_date = datetime.now().strftime("%Y-%m-%d")
        
        query = """
            SELECT id, appointment_date, start_time, end_time, attendee_email, 
                attendee_name, title, description, status, created_at
            FROM appointments
            WHERE user_id = %s AND appointment_date >= %s
            ORDER BY appointment_date, start_time
        """
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query, (user_id, from_date))
                return cursor.fetchall()

    def check_appointment_conflict(
        self,
        user_id: int,
        appointment_date: str,
        start_time: str,
        end_time: str
    ) -> bool:
        """Check if there's a conflicting appointment"""
        query = """
            SELECT COUNT(*) as conflict_count
            FROM appointments
            WHERE user_id = %s 
            AND appointment_date = %s
            AND status = 'scheduled'
            AND (
                (start_time <= %s AND end_time > %s) OR
                (start_time < %s AND end_time >= %s) OR
                (start_time >= %s AND end_time <= %s)
            )
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (
                    user_id, appointment_date,
                    start_time, start_time,
                    end_time, end_time,
                    start_time, end_time
                ))
                result = cursor.fetchone()
                return result[0] > 0

    def get_available_slots(
        self,
        user_id: int,
        appointment_date: str,
        business_hours_start: str = "08:00",
        business_hours_end: str = "18:00",
        slot_duration_minutes: int = 60
    ):
        """Get available time slots for a given date"""
        query = """
            SELECT start_time, end_time
            FROM appointments
            WHERE user_id = %s AND appointment_date = %s AND status = 'scheduled'
            ORDER BY start_time
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (user_id, appointment_date))
                booked_slots = cursor.fetchall()
        
        # Logic to calculate available slots (simplified)
        # You can expand this to generate proper available slots
        return {
            "date": appointment_date,
            "booked_slots": [{"start": slot[0], "end": slot[1]} for slot in booked_slots]
        }


    def get_call_by_id(self, call_id: str, user_id: int):
        """Get a specific call by ID for a user"""
        query = """
            SELECT id, call_id, status, duration, transcript, recording_url, 
                transcript_url, transcript_blob, recording_blob,
                created_at, started_at, ended_at, 
                from_number, to_number, voice_name
            FROM call_history
            WHERE call_id = %s AND user_id = %s
        """
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query, (call_id, user_id))
                result = cursor.fetchone()
                
                # Parse transcript if it's a string
                if result and isinstance(result.get("transcript"), str):
                    try:
                        result["transcript"] = json.loads(result["transcript"])
                    except:
                        pass
                
                return result
















































# import os
# from datetime import datetime
# import bcrypt
# import urllib.parse
# import json
# import psycopg2
# import logging
# from psycopg2.extras import RealDictCursor
# from dotenv import load_dotenv

# load_dotenv()

# class PGDB:
#     def __init__(self):
#         # host = os.getenv('HOST')
#         # port = os.getenv('DB_PORT')
#         # user = os.getenv('USER')
#         # password = os.getenv('PASSWORD')
#         # db_name = os.getenv('DATABASE_NAME')

#         # if not all([host, port, user, password, db_name]):
#         #     raise ValueError("Missing required database environment variables")

#         # password_encoded = urllib.parse.quote_plus(password)
#         # self.connection_string = f"postgresql://{user}:{password_encoded}@{host}:{port}/{db_name}"
#         self.connection_string = os.getenv('DATABASE_URL')

#         self.create_users_table()
#         self.create_call_history_table()
#     def get_connection(self):
#         try:
#             return psycopg2.connect(self.connection_string)
#         except Exception as e:
#             logging.error(f"Database connection error: {e}")
#             raise

#     def create_users_table(self):
#         conn = self.get_connection()
#         try:
#             with conn.cursor() as cursor:
#                 cursor.execute("""
#                     CREATE TABLE IF NOT EXISTS users (
#                         id SERIAL PRIMARY KEY,
#                         username VARCHAR(100),
#                         email VARCHAR(100) UNIQUE NOT NULL,
#                         password_hash TEXT NOT NULL,
#                         first_name VARCHAR(100),
#                         last_name VARCHAR(100),
#                         is_admin BOOLEAN DEFAULT FALSE,
#                         created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
#                         updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
#                     );
#                 """)
#             conn.commit()
#         except Exception as e:
#             logging.error(f"Error creating users table: {e}")
#         finally:
#             conn.close()


#     def create_call_history_table(self):
#         """
#         Create call_history table to store details of calls 
#         (caller, callee, status, duration, transcript, recording, etc.)
#         """
#         conn = self.get_connection()
#         try:
#             with conn.cursor() as cursor:
#                 cursor.execute("""
#                     CREATE TABLE IF NOT EXISTS call_history (
#                         id SERIAL PRIMARY KEY,
#                         user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
#                         call_id TEXT NOT NULL,
#                         status TEXT,
#                         duration INTEGER,
#                         transcript JSONB,
#                         summary TEXT,
#                         recording_url TEXT,
#                         created_at TIMESTAMPTZ,
#                         started_at TIMESTAMPTZ,
#                         ended_at TIMESTAMPTZ,
#                         voice_id TEXT,
#                         voice_name TEXT,
#                         from_number TEXT,   -- caller number
#                         to_number TEXT,     -- receiver number
#                         inserted_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
#                     );
#                 """)
#             conn.commit()
#         except Exception as e:
#             logging.error(f"Error creating call_history table: {e}")
#         finally:
#             conn.close()



#     # ============================= USERS LOGIC START =============================


#     def register_user(self, user_data):
#         conn = self.get_connection()
#         try:
#             with conn.cursor(cursor_factory=RealDictCursor) as cursor:
#                 # Check if email already exists
#                 cursor.execute("SELECT id FROM users WHERE email = %s", (user_data['email'],))
#                 if cursor.fetchone():
#                     raise ValueError("Email already registered.")

#                 # Hash the password
#                 hashed_password = bcrypt.hashpw(user_data['password'].encode('utf-8'), bcrypt.gensalt())

#                 # Insert user
#                 cursor.execute("""
#                     INSERT INTO users (username, email, password_hash,is_admin)
#                     VALUES (%s, %s, %s,%s)
#                     RETURNING id, username, email, created_at,is_admin;
#                 """, (
#                     user_data['username'],
#                     user_data['email'],
#                     hashed_password.decode('utf-8'),
#                     user_data['is_admin']
#                 ))

#                 row = cursor.fetchone()
#                 conn.commit()

#                 # Match output with UserOut model
#                 return {
#                     "id": row["id"],
#                     "username": row["username"],
#                     "email": row["email"],
#                     "created_at": row["created_at"]
#                 }

#         except Exception as e:
#             conn.rollback()
#             logging.error(f"Error in register_user: {e}")
#             raise
#         finally:
#             conn.close()

#     def login_user(self, user_data):
#         """Verify user credentials by username or email and return user info."""
#         conn = self.get_connection()
#         try:
#             with conn.cursor() as cursor:
#                 cursor.execute("""
#                     SELECT id, username, email, password_hash,first_name,last_name,created_at,is_admin
#                     FROM users
#                     WHERE username = %s OR email = %s
#                     LIMIT 1
#                 """, (user_data.get("username"), user_data['email']))

#                 result = cursor.fetchone()

#                 if result and bcrypt.checkpw(user_data['password'].encode('utf-8'), result[3].encode('utf-8')):
#                     return {
#                         "id": result[0],
#                         "username": result[1],
#                         "email": result[2],
#                         # "first_name": result[4],
#                         # "last_name": result[5],
#                         "created_at": result[6],
#                         "is_admin": result[7]
#                     }
#                 else:
#                     raise ValueError("Invalid username or password.")
#         except Exception as e:
#             logging.error(f"Error during login: {str(e)}")
#             raise
#         finally:
#             conn.close()


#     def get_user_by_id(self, user_id: int):
#         """Get user by ID"""
#         conn = self.get_connection()
#         try:
#             with conn.cursor(cursor_factory=RealDictCursor) as cursor:
#                 cursor.execute(
#                     "SELECT id,first_name,last_name,username,email,is_admin,created_at FROM users WHERE id = %s",
#                     (user_id,)
#                 )
#                 return cursor.fetchone()
#         finally:
#             if conn:
#                 conn.close()

#     def delete_user_by_id(self,user_id):
#         """
#         delete user by id
#         """
#         conn = None 
#         try:
#             conn = self.get_connection()
#             with conn.cursor() as cursor:
#                 cursor.execute(
#                     """
#                     DELETE FROM users WHERE id = %s
#                     """,
#                     (user_id,)      
#                 )
#             conn.commit()
#             return True
#         except Exception as e:
#             logging.error(f"Error deleting user {user_id}: {e}")
#             if conn:
#                 conn.rollback()
#                 return False
#         finally:
#             if conn:
#                 conn.close()

#     def update_user_name_fields(self, user_id: int, first_name: str, last_name: str):
#         conn = self.get_connection()
#         try:
#             with conn.cursor() as cursor:
#                 cursor.execute("""
#                     UPDATE users
#                     SET first_name = %s,
#                         last_name = %s,
#                         updated_at = CURRENT_TIMESTAMP
#                     WHERE id = %s
#                 """, (first_name, last_name, user_id))
#             conn.commit()
#             return True
#         except Exception as e:
#             logging.error(f"Error updating name fields: {e}")
#             return False
#         finally:
#             conn.close()

#     def change_user_password(self, user_id: int, current_password: str, new_password: str):
#         conn = self.get_connection()
#         try:
#             with conn.cursor() as cursor:
#                 cursor.execute("""
#                     SELECT password_hash FROM users WHERE id = %s
#                 """, (user_id,))
#                 result = cursor.fetchone()
#                 if not result:
#                     raise ValueError("User not found.")

#                 # Verify current password
#                 if not bcrypt.checkpw(current_password.encode(), result[0].encode()):
#                     raise ValueError("Current password is incorrect.")

#                 # Hash new password
#                 new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

#                 # Update
#                 cursor.execute("""
#                     UPDATE users SET password_hash = %s, updated_at = CURRENT_TIMESTAMP
#                     WHERE id = %s
#                 """, (new_hash, user_id))
#             conn.commit()
#             return True
#         except Exception as e:
#             logging.error(f"Password change error: {e}")
#             raise
#         finally:
#             conn.close()            

#     def get_all_users(self):
#         query = """
#             SELECT id, first_name, last_name, username, email, is_admin, created_at
#             FROM users
#             WHERE is_admin = FALSE
#             ORDER BY created_at DESC
#         """
#         conn = self.get_connection()
#         try:
#             with conn.cursor() as cursor:
#                 cursor.execute(query)
#                 result = cursor.fetchall()
#                 return [
#                     {
#                         "id": row[0],
#                         # "first_name": row[1],
#                         # "last_name": row[2],
#                         "username": row[3],
#                         "email": row[4],
#                         "is_admin": row[5],
#                         "created_at": row[6],
#                     }
#                     for row in result
#                 ]
#         finally:
#             conn.close()

#     def get_all_users_paginated(self, page: int = 1, page_size: int = 10):
#         query_total = "SELECT COUNT(*) FROM users WHERE is_admin = FALSE"
#         query_data = """
#             SELECT id, first_name, last_name, username, email, is_admin, created_at
#             FROM users
#             -- WHERE is_admin = FALSE
#             ORDER BY created_at DESC
#             LIMIT %s OFFSET %s
#         """

#         conn = self.get_connection()
#         try:
#             with conn.cursor() as cursor:
#                 # Total user count
#                 cursor.execute(query_total)
#                 total_users = cursor.fetchone()[0]

#                 # Paginated data
#                 offset = (page - 1) * page_size
#                 cursor.execute(query_data, (page_size, offset))
#                 rows = cursor.fetchall()

#                 users = [
#                     {
#                         "id": row[0],
#                         # "first_name": row[1],
#                         # "last_name": row[2],
#                         "username": row[3],
#                         "email": row[4],
#                         "is_admin": row[5],
#                         "created_at": row[6],
#                     }
#                     for row in rows
#                 ]

#             return {
#                 "users": users,
#                 "total": total_users
#             }

#         except Exception as e:
#             print(f"Error fetching paginated users: {e}")
#             return {"users": [], "total": 0}
#         finally:
#             conn.close()            


#     # ================================ call history logic ==================================

#     # def insert_call_history(
#     #     self,
#     #     user_id: int,
#     #     call_id: str,
#     #     status: str = None,
#     #     duration: int = None,
#     #     transcript: dict = None,
#     #     summary: str = None,
#     #     recording_url: str = None,
#     #     created_at: str = None,
#     #     started_at: str = None,
#     #     ended_at: str = None,
#     #     voice_id: str = None,
#     #     voice_name: str = None,
#     #     from_number: str = None,
#     #     to_number: str = None
#     # ):
#     #     """
#     #     Insert a new call history record into the database.
#     #     """
#     #     conn = self.get_connection()
#     #     try:
#     #         with conn.cursor() as cursor:
#     #             values = (
#     #                 user_id, call_id, status, duration,
#     #                 json.dumps(transcript) if transcript else None,
#     #                 summary, recording_url,
#     #                 created_at, started_at, ended_at,
#     #                 voice_id, voice_name,
#     #                 from_number, to_number
#     #             )

#     #             # Debug log
#     #             logging.debug(f"Inserting call_history with values: {values}")

#     #             cursor.execute("""
#     #                 INSERT INTO call_history (
#     #                     user_id, call_id, status, duration, transcript, summary,
#     #                     recording_url, created_at, started_at, ended_at,
#     #                     voice_id, voice_name,
#     #                     from_number, to_number
#     #                 )
#     #                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
#     #                 RETURNING id;
#     #             """, values)

#     #             row = cursor.fetchone()
#     #             conn.commit()

#     #             if not row:
#     #                 logging.error("Insert returned no row (check schema/columns)")
#     #                 return None

#     #             return row[0]

#     #     except Exception as e:
#     #         logging.error(f"Error inserting call history: {e}")
#     #         conn.rollback()
#     #         raise
#     #     finally:
#     #         conn.close()

#     def insert_call_history(
#         self, user_id: int, call_id: str, status: str,
#         voice_id: str, voice_name: str, to_number: str
#     ):
#         conn = self.get_connection()
#         try:
#             with conn.cursor() as cursor:
#                 cursor.execute("""
#                     INSERT INTO call_history (
#                         user_id, call_id, status,
#                         voice_id, voice_name, to_number, created_at
#                     )
#                     VALUES (%s,%s,%s,%s,%s,%s,NOW())
#                     RETURNING id;
#                 """, (user_id, call_id, status, voice_id, voice_name, to_number))
#                 row = cursor.fetchone()
#                 conn.commit()
#                 return row[0]
#         except Exception as e:
#             logging.error(f"Error inserting call history: {e}")
#             conn.rollback()
#             raise
#         finally:
#             conn.close()


#     def get_call_history_by_user_id(self, user_id: int):
#         """
#         Fetch call history for a user (with JOIN on users table).
#         Includes call details, voice info, caller/callee numbers, and timestamps.
#         """
#         conn = self.get_connection()
#         try:
#             with conn.cursor(cursor_factory=RealDictCursor) as cursor:
#                 cursor.execute("""
#                     SELECT ch.id, ch.call_id, ch.status, ch.duration, ch.transcript,
#                         ch.summary, ch.recording_url, ch.created_at, ch.started_at, ch.ended_at,
#                         ch.voice_id, ch.voice_name, ch.from_number, ch.to_number,
#                         u.id AS user_id, u.username, u.email
#                     FROM call_history ch
#                     JOIN users u ON ch.user_id = u.id
#                     WHERE ch.user_id = %s
#                     ORDER BY ch.created_at DESC
#                 """, (user_id,))
                
#                 rows = cursor.fetchall()

#                 # Convert transcript from string to JSON if needed
#                 for row in rows:
#                     if isinstance(row["transcript"], str):
#                         try:
#                             row["transcript"] = json.loads(row["transcript"])
#                         except Exception:
#                             logging.warning(f"Invalid JSON in transcript for call_id={row['call_id']}")
                
#                 return rows

#         except Exception as e:
#             logging.error(f"Error fetching call history for user_id={user_id}: {e}")
#             raise
#         finally:
#             conn.close()
                    

#     # def update_call_status(self, call_id: str, status: str):
#     #     conn = self.get_connection()
#     #     try:
#     #         with conn.cursor() as cursor:
#     #             cursor.execute("""
#     #                 UPDATE call_history
#     #                 SET status = %s, updated_at = CURRENT_TIMESTAMP
#     #                 WHERE call_id = %s
#     #             """, (status, call_id))
#     #         conn.commit()
#     #     except Exception as e:
#     #         logging.error(f"Error updating call status: {e}")
#     #         conn.rollback()
#     #         raise
#     #     finally:
#     #         conn.close()


#     # def update_call_status(self, call_id: str, status: str):
#     #     conn = self.get_connection()
#     #     try:
#     #         with conn.cursor() as cursor:
#     #             cursor.execute("""
#     #                 UPDATE call_history
#     #                 SET status = %s, updated_at = NOW()
#     #                 WHERE call_id = %s
#     #             """, (status, call_id))
#     #         conn.commit()
#     #     except Exception as e:
#     #         logging.error(f"Error updating call status: {e}")
#     #         conn.rollback()
#     #         raise
#     #     finally:
#     #         conn.close()

#     def update_call_status(self, call_id: str, status: str):
#         conn = self.get_connection()
#         try:
#             with conn.cursor() as cursor:
#                 cursor.execute("""
#                     UPDATE call_history
#                     SET status = %s
#                     WHERE call_id = %s
#                 """, (status, call_id))
#             conn.commit()
#         except Exception as e:
#             logging.error(f"Error updating call status: {e}")
#             conn.rollback()
#             raise
#         finally:
#             conn.close()



#     # def update_call_details(
#     #     self, call_id: str, status: str, duration: float,
#     #     summary: str, transcript: list, recording_url: str,
#     #     started_at: str, ended_at: str, from_number: str
#     # ):
#     #     conn = self.get_connection()
#     #     try:
#     #         with conn.cursor() as cursor:
#     #             cursor.execute("""
#     #                 UPDATE call_history
#     #                 SET status=%s, duration=%s, summary=%s, transcript=%s,
#     #                     recording_url=%s, started_at=%s, ended_at=%s,
#     #                     from_number=%s, updated_at=NOW()
#     #                 WHERE call_id=%s
#     #             """, (
#     #                 status, duration, summary,
#     #                 json.dumps(transcript) if transcript else None,
#     #                 recording_url, started_at, ended_at, from_number, call_id
#     #             ))
#     #         conn.commit()
#     #     except Exception as e:
#     #         logging.error(f"Error updating call details: {e}")
#     #         conn.rollback()
#     #         raise
#     #     finally:
#     #         conn.close()

#     def update_call_details(
#         self, call_id: str, status: str, duration: int,
#         summary: str, transcript: list, recording_url: str,
#         started_at: str, ended_at: str, from_number: str, to_number: str
#     ):
#         conn = self.get_connection()
#         try:
#             with conn.cursor() as cursor:
#                 cursor.execute("""
#                     UPDATE call_history
#                     SET status=%s,
#                         duration=%s,
#                         summary=%s,
#                         transcript=%s,
#                         recording_url=%s,
#                         started_at=%s,
#                         ended_at=%s,
#                         from_number=%s,
#                         to_number=%s
#                     WHERE call_id=%s
#                 """, (
#                     status, duration, summary,
#                     json.dumps(transcript) if transcript else None,
#                     recording_url, started_at, ended_at,
#                     from_number, to_number, call_id
#                 ))
#             conn.commit()
#         except Exception as e:
#             logging.error(f"Error updating call details: {e}")
#             conn.rollback()
#             raise
#         finally:
#             conn.close()
