import sqlite3, time, re, json, csv, io
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import sqlglot
from sqlglot import exp

ROOT=Path(__file__).resolve().parents[2]; DATA=ROOT/'data'; DATA.mkdir(exist_ok=True)
DB=DATA/'demo.sqlite3'; HIST=DATA/'history.sqlite3'
app=FastAPI(title='Compiler-Driven SQL Query Performance Enhancement System')
SCHEMA={'students':['studentid','studentname','departmentid','marks'],'departments':['departmentid','departmentname'],'courses':['courseid','coursename','departmentid'],'enrollments':['enrollmentid','studentid','courseid','enrollmentdate']}
SAMPLES=[{'name':'Student results by department','sql':'SELECT s.StudentName, d.DepartmentName, s.Marks FROM Students s JOIN Departments d ON s.DepartmentID = d.DepartmentID WHERE s.Marks > 75 ORDER BY s.Marks DESC LIMIT 20;'}, {'name':'Enrollment counts','sql':'SELECT c.CourseName, COUNT(e.EnrollmentID) AS enrollment_count FROM Courses c JOIN Enrollments e ON c.CourseID = e.CourseID GROUP BY c.CourseID, c.CourseName ORDER BY enrollment_count DESC;'}, {'name':'Repeated filter demo','sql':'SELECT StudentName, Marks FROM Students WHERE Marks >= 80 AND Marks >= 80 AND DepartmentID = 1 ORDER BY Marks DESC;'}, {'name':'Department averages','sql':'SELECT d.DepartmentName, AVG(s.Marks) AS average_marks FROM Departments d JOIN Students s ON d.DepartmentID = s.DepartmentID GROUP BY d.DepartmentID, d.DepartmentName HAVING AVG(s.Marks) > 60;'}]
def conn(path=DB):
 c=sqlite3.connect(path,timeout=2); c.row_factory=sqlite3.Row; return c
def init():
 c=conn(); c.executescript('CREATE TABLE IF NOT EXISTS Departments(DepartmentID INTEGER PRIMARY KEY,DepartmentName TEXT); CREATE TABLE IF NOT EXISTS Students(StudentID INTEGER PRIMARY KEY,StudentName TEXT,DepartmentID INTEGER,Marks INTEGER); CREATE TABLE IF NOT EXISTS Courses(CourseID INTEGER PRIMARY KEY,CourseName TEXT,DepartmentID INTEGER); CREATE TABLE IF NOT EXISTS Enrollments(EnrollmentID INTEGER PRIMARY KEY,StudentID INTEGER,CourseID INTEGER,EnrollmentDate TEXT);')
 if not c.execute('SELECT COUNT(*) FROM Departments').fetchone()[0]:
  c.executemany('INSERT INTO Departments VALUES (?,?)',[(1,'Computer Science'),(2,'Mathematics'),(3,'Physics'),(4,'Biology')]); c.executemany('INSERT INTO Students VALUES (?,?,?,?)',[(i,f'Student {i:03}',i%4+1,45+i*17%56) for i in range(1,401)]); c.executemany('INSERT INTO Courses VALUES (?,?,?)',[(i,n,i%4+1) for i,n in enumerate(['Algorithms','Databases','Calculus','Statistics','Networks','AI','Mechanics','Genetics'],1)]); c.executemany('INSERT INTO Enrollments VALUES (?,?,?,?)',[(i,(i*7)%400+1,(i*3)%8+1,f'2026-{i%12+1:02}-{i%27+1:02}') for i in range(1,1601)])
 c.commit(); c.close(); h=sqlite3.connect(HIST); h.execute('CREATE TABLE IF NOT EXISTS analyses(id INTEGER PRIMARY KEY,created TEXT DEFAULT CURRENT_TIMESTAMP,original_sql TEXT,optimized_sql TEXT,report TEXT,status TEXT,original_ms REAL,optimized_ms REAL,improvement REAL)'); h.commit(); h.close()
init()
class Req(BaseModel): sql:str=Field(min_length=1,max_length=20000); runs:int=Field(default=3,ge=1,le=10)
def parse(sql):
 try: ts=sqlglot.parse(sql,read='sqlite')
 except Exception as e: raise HTTPException(400,f'SQL parse error: {e}')
 if len(ts)!=1 or ts[0] is None: raise HTTPException(400,'Submit exactly one SQL statement.')
 t=ts[0]
 if not isinstance(t,exp.Select) or any(isinstance(n,(exp.Insert,exp.Update,exp.Delete,exp.Drop,exp.Alter,exp.Create,exp.Command)) for n in t.walk()): raise HTTPException(403,'Only read-only SELECT queries are allowed.')
 return t
def lex(s):
 p=r"(?P<space>\s+)|(?P<comment>--[^\n]*|/\*[\s\S]*?\*/)|(?P<string>'(?:''|[^'])*')|(?P<number>\b\d+(?:\.\d+)?\b)|(?P<ident>\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][\w$]*)|(?P<operator><=|>=|<>|!=|[-+*/%=<>])|(?P<punct>[(),.;])|(?P<other>.)"
 out=[]; line=col=1
 for m in re.finditer(p,s):
  v=m.group(); k=m.lastgroup
  if k=='space':
   n=v.count('\n'); line+=n; col=len(v.rsplit('\n',1)[-1])+1 if n else col+len(v); continue
  typ={'comment':'COMMENT','string':'LITERAL','number':'LITERAL','operator':'OPERATOR','punct':'PUNCTUATION','other':'UNKNOWN'}.get(k)
  if k=='ident': typ='KEYWORD' if v.upper() in 'SELECT FROM WHERE JOIN LEFT RIGHT INNER ON AND OR NOT AS GROUP BY ORDER HAVING LIMIT OFFSET DISTINCT ASC DESC IS IN LIKE NULL UNION'.split() else ('FUNCTION' if v.upper() in ['COUNT','AVG','SUM','MIN','MAX'] else 'IDENTIFIER')
  out.append({'number':len(out)+1,'type':typ,'value':v,'line':line,'column':col}); n=v.count('\n'); line+=n; col=len(v.rsplit('\n',1)[-1])+1 if n else col+len(v)
 return out
def semantic(t):
 names=[]; errs=[]; aliases={}
 for x in t.find_all(exp.Table):
  n=x.name.lower()
  if n not in SCHEMA: errs.append(f"Table '{x.name}' is not in the demo schema.")
  else: names.append(n); aliases[(x.alias_or_name or x.name).lower()]=n
 for x in t.find_all(exp.Column):
  n=x.name.lower()
  if n=='*': continue
  tb=aliases.get(x.table.lower()) if x.table else None
  if x.table and tb and n not in SCHEMA[tb]: errs.append(f"Column '{x.sql()}' does not exist.")
  elif not x.table and names and not any(n in SCHEMA[z] for z in names) and n not in ['enrollment_count','average_marks']: errs.append(f"Column '{x.name}' was not found in referenced tables.")
 return sorted(set(names)),sorted(set(errs))
def execute(sql,runs):
 c=conn(); c.execute('PRAGMA query_only=ON'); deadline=[0.0]
 c.set_progress_handler(lambda: 1 if time.perf_counter()>deadline[0] else 0,1000)
 plan=[dict(x) for x in c.execute('EXPLAIN QUERY PLAN '+sql)]; ms=[]; data=[]
 for _ in range(runs):
  start=time.perf_counter(); deadline[0]=start+2.0; data=c.execute(sql).fetchmany(501); ms.append((time.perf_counter()-start)*1000)
  if len(data)>500: c.close(); raise HTTPException(400,'Query returned over 500 rows; add LIMIT.')
 c.close(); return sum(ms)/len(ms),len(data),plan,[dict(x) for x in data[:50]]
@app.get('/api/meta')
def meta(): return {'samples':SAMPLES,'schema':SCHEMA}
@app.post('/api/analyze')
def analyze(q:Req):
 t=parse(q.sql); tables,errs=semantic(t); original=t.sql(dialect='sqlite'); opt=t.copy(); removed=[]
 # Remove repeated predicates only inside AND expressions.
 for node in list(opt.find_all(exp.And)):
  parts=[]
  def flat(n):
   if isinstance(n,exp.And): flat(n.this); flat(n.expression)
   else: parts.append(n)
  flat(node); unique=[]; seen=set()
  for x in parts:
   key=x.sql().lower()
   if key in seen: removed.append(key)
   else: seen.add(key); unique.append(x)
  if len(unique)<len(parts):
   repl=unique[0]
   for x in unique[1:]: repl=exp.and_(repl,x)
   node.replace(repl)
 optimized=opt.sql(dialect='sqlite'); om=orows=oplan=rows=None; nm=nrows=nplan=None
 if not errs:
  try: om,orows,oplan,rows=execute(original,q.runs); nm,nrows,nplan,_=execute(optimized,q.runs)
  except sqlite3.Error as e: raise HTTPException(400,f'Demo execution error: {e}')
 imp=(om-nm)/om*100 if om and nm is not None else None
 recommendations=[]
 for c in t.find_all(exp.Column):
  if c.name!='*' and (c.find_ancestor(exp.Where) or c.find_ancestor(exp.Join) or c.find_ancestor(exp.Order)):
   recommendations.append({'title':f'Consider an index on {c.table or "table"}.{c.name}','reason':'This column participates in filtering, joining, or ordering. Validate with representative workload.'})
 ir={'kind':'SELECT','tables':tables,'projections':[x.sql() for x in t.expressions],'filters':[x.sql() for x in t.find_all(exp.Where)],'joins':[x.sql() for x in t.find_all(exp.Join)]}
 report={'original_sql':original,'optimized_sql':optimized,'tokens':lex(q.sql),'statement_type':'SELECT','syntax_valid':True,'semantic_valid':not errs,'semantic_errors':errs,'tables':tables,'ast':t.dump(),'ir':ir,'transformations':['Duplicate predicate removal'] if removed else [],'changes':[f'Removed duplicate predicate: {x}' for x in sorted(set(removed))],'recommendations':recommendations,'original_ms':om,'optimized_ms':nm,'improvement_pct':imp,'rows_returned':orows,'optimized_rows_returned':nrows,'original_plan':oplan,'optimized_plan':nplan,'sample_rows':rows,'timing_runs':q.runs,'cost_note':'SQLite EXPLAIN QUERY PLAN is qualitative; numeric I/O, memory, and CPU costs are not available.'}
 h=sqlite3.connect(HIST); cur=h.execute('INSERT INTO analyses(original_sql,optimized_sql,report,status,original_ms,optimized_ms,improvement) VALUES (?,?,?,?,?,?,?)',(original,optimized,json.dumps(report),'valid' if not errs else 'semantic_error',om,nm,imp)); report['id']=cur.lastrowid; h.commit(); h.close(); return report
@app.get('/api/history')
def history(q:str=''):
 h=sqlite3.connect(HIST); h.row_factory=sqlite3.Row; a=[dict(x) for x in h.execute('SELECT id,created,original_sql,optimized_sql,status,original_ms,optimized_ms,improvement FROM analyses WHERE original_sql LIKE ? ORDER BY id DESC LIMIT 100',(f'%{q}%',))]; h.close(); return a
@app.get('/api/history/{i}')
def get_history(i:int):
 h=sqlite3.connect(HIST); r=h.execute('SELECT report FROM analyses WHERE id=?',(i,)).fetchone(); h.close()
 if not r: raise HTTPException(404,'Not found')
 return json.loads(r[0])
@app.delete('/api/history/{i}')
def delete(i:int):
 h=sqlite3.connect(HIST); h.execute('DELETE FROM analyses WHERE id=?',(i,)); h.commit(); h.close(); return {'deleted':i}
@app.get('/api/stats')
def stats():
 h=sqlite3.connect(HIST); r=h.execute('SELECT COUNT(*),SUM(original_sql!=optimized_sql),AVG(original_ms),AVG(improvement) FROM analyses').fetchone(); h.close(); return {'total':r[0] or 0,'optimized':r[1] or 0,'avg_ms':r[2],'avg_improvement':r[3]}
@app.get('/api/export.csv')
def export_csv():
 h=sqlite3.connect(HIST); rows=h.execute('SELECT id,created,original_sql,optimized_sql,status,original_ms,optimized_ms,improvement FROM analyses ORDER BY id DESC').fetchall(); h.close(); s=io.StringIO(); w=csv.writer(s); w.writerow(['id','created','original_sql','optimized_sql','status','original_ms','optimized_ms','improvement_pct']); w.writerows(rows); return Response(s.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=query-history.csv'})
app.mount('/',StaticFiles(directory=ROOT/'frontend',html=True),name='frontend')
