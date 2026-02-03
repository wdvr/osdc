FROM python:3.11-slim

WORKDIR /app

RUN pip install flask

COPY server.py /app/
COPY templates /app/templates/

# Create flag for this layer
RUN mkdir -p /flags
RUN echo "FLAG{p1v0t_p01nt_d1sc0v3r3d}" > /flags/flag4.txt

# Store agent endpoint info
ENV AGENT_ENDPOINT=http://layer5-agent:6666
ENV PIVOT_KEY=ESCALATION-COMPLETE-2024

EXPOSE 7777

CMD ["python", "server.py"]
