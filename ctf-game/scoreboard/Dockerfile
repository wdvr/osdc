FROM python:3.11-slim

WORKDIR /app

RUN pip install flask

COPY app.py /app/
COPY templates /app/templates/

# Flags for validation
ENV FLAG1="FLAG{1nf0_d1scl0sur3_1s_r34l}"
ENV FLAG2="FLAG{r3str1ct3d_sh3ll_3sc4p3}"
ENV FLAG3="FLAG{pr1v1l3g3_3sc4l4t10n_m4st3r}"
ENV FLAG4="FLAG{p1v0t_p01nt_d1sc0v3r3d}"
ENV FLAG5="FLAG{pr0mpt_1nj3ct10n_m4st3r_h4ck3r}"

EXPOSE 8000

CMD ["python", "app.py"]
