"""Author + title for every issue in the window (anonymous GraphQL). Writes authors.jsonl."""
import json, sys, time, urllib.request
Q="""query($after:String){ project(fullPath:"gitlab-org/gitlab"){
  issues(createdAfter:"2025-01-01T00:00:00Z", createdBefore:"2025-07-01T00:00:00Z", first:100, after:$after, sort:CREATED_ASC){
    pageInfo{ hasNextPage endCursor } nodes{ iid title author{ username } } } } }"""
after=None
with open("authors.jsonl","w") as f:
    while True:
        b=json.dumps({"query":Q,"variables":{"after":after}}).encode()
        for a in range(5):
            try: d=json.load(urllib.request.urlopen(urllib.request.Request("https://gitlab.com/api/graphql",b,{"Content-Type":"application/json"}),timeout=60)); break
            except Exception as e: time.sleep(5*(a+1))
        p=d["data"]["project"]["issues"]
        for n in p["nodes"]: f.write(json.dumps({"iid":n["iid"],"title":n["title"],"author":(n["author"] or {}).get("username")})+"\n")
        if not p["pageInfo"]["hasNextPage"]: break
        after=p["pageInfo"]["endCursor"]; time.sleep(0.2)
