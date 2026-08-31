"""Explicit, editable workflow taxonomy, not inferred compliance decisions."""
import re

STAGES = [
 {'id':'planning','name':'站址与规划','en':'SITE & PLANNING','icon':'compass','description':'站址条件、总平面与规划边界','keywords':['站址','选址','规划','总平面','总布置','地质','洪水','地震','用地'],'questions':['变电站站址选择有哪些要求？','总平面布置应考虑哪些因素？']},
 {'id':'layout','name':'总图与间距','en':'LAYOUT & CLEARANCE','icon':'layout','description':'建筑布置、防火间距与安全边界','keywords':['间距','距离','布置','主变压器','围墙','防火墙','建筑物'],'questions':['变电站总平面布置的防火间距有哪些要求？','主变压器与建筑物的布置应注意什么？']},
 {'id':'roads','name':'道路与竖向','en':'ROADS & GRADING','icon':'route','description':'站内道路、竖向布置与场地排水','keywords':['道路','消防车道','转弯','竖向','坡度','排水','场地','土石方'],'questions':['站内道路和消防车道有哪些设计要求？','竖向布置和场地排水应遵循哪些规定？']},
 {'id':'systems','name':'设施与管线','en':'SYSTEMS & UTILITIES','icon':'layers','description':'管沟、电缆及辅助设施协调','keywords':['管线','管沟','电缆','管道','沟道','地下','交叉','辅助'],'questions':['管线与沟道综合布置有哪些要求？','电缆沟与其他管线交叉时应注意什么？']},
 {'id':'fire','name':'消防与安全','en':'FIRE & SAFETY','icon':'shield','description':'防火分区、疏散与消防设施','keywords':['防火','消防','灭火','疏散','火灾','安全出口','耐火','储能','电池'],'questions':['电化学储能电站的消防设计有哪些要求？','建筑安全疏散有哪些规定？']},
 {'id':'review','name':'校核与交付','en':'REVIEW & DELIVERY','icon':'check','description':'原文复核、版本确认与证据归档','keywords':['应','不应','不得','必须','审查','校核','强制'],'questions':['设计校核时如何核对规范版本和条文依据？']},
]
TOPICS = ['防火间距','消防车道','防火墙','主变压器','变电站','储能','电池','安全出口','疏散','灭火','管线','电缆','排水','竖向','道路','站址','总平面','耐火等级','建筑物','防火分区']
ALIASES = {'消防通道':'消防车道','消防道路':'消防车道','蓄电池':'电池','变电所':'变电站','总图':'总平面','退距':'间距','电缆隧道':'电缆沟'}
CLAUSE = re.compile(r'(?m)^\s*((?:\d{1,2}\.){2,4}\d{1,3})\s*(?=[^\d.])')

def normalize(text):
    text = text.lower()
    for a, b in ALIASES.items():
        text = text.replace(a,b)
    return text

def classify(text):
    topics = [x for x in TOPICS if x in text]
    scores = [(sum(text.count(k) for k in s['keywords']),s['id']) for s in STAGES if s['id']!='review']
    stages = [sid for score,sid in sorted(scores,reverse=True) if score>0][:3]
    return stages or ['review'], topics

def tokens(text):
    text = normalize(text)
    out = re.findall(r'[a-z0-9]+(?:[.\-/][a-z0-9]+)*', text)
    for seq in re.findall(r'[\u3400-\u9fff]+',text):
        out.extend(seq[i:i+2] for i in range(len(seq)-1))
        out.extend(seq[i:i+3] for i in range(len(seq)-2))
    return out
