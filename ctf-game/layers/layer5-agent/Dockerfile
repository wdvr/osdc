FROM python:3.11-slim

WORKDIR /app

RUN pip install flask

COPY agent.py /app/
COPY prompts.py /app/

# The final secret
ENV FINAL_SECRET="FLAG{pr0mpt_1nj3ct10n_m4st3r_h4ck3r}"

# Create flag file
RUN mkdir -p /flags
RUN echo "FLAG{pr0mpt_1nj3ct10n_m4st3r_h4ck3r}" > /flags/flag5.txt

EXPOSE 6666

CMD ["python", "agent.py"]
