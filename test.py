from db_config import get_connection_string
import pyodbc

conn = pyodbc.connect(get_connection_string())
cursor = conn.cursor()

cursor.execute("USE mercury;")
cursor.execute("""
SELECT EmployeeName, AD
FROM dbo.Employee
WHERE UPPER(AD)=UPPER(?)
""", ("m-mori",))

print(cursor.fetchone())
