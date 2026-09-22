FROM python:3.11-slim

# Install system dependencies untuk C compiler dan Nuitka
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir nuitka

# Copy seluruh source code
COPY . .

# Kompilasi aplikasi menggunakan Nuitka
# --module: Mengompilasi menjadi extension module (.so)
# --follow-import-to=app: Hanya ikuti import yang menuju ke package 'app'
# --include-package=app: Masukkan seluruh isi folder 'app'
RUN python -m nuitka --module run.py \
    --follow-import-to=app \
    --include-package=app \
    --nofollow-imports \
    --output-dir=build_output

# Langkah Pembersihan Penting:
# 1. Hapus semua file .py (kecuali __init__.py jika diperlukan, tapi biasanya .so sudah cukup)
# 2. Pindahkan file hasil kompilasi (.so) ke root agar gunicorn bisa menemukannya
# 3. Hapus folder build untuk menghemat space
RUN find . -name "*.py" -delete && \
    cp build_output/run*.so ./run.so && \
    rm -rf build_output

# Expose port yang digunakan Railway
EXPOSE 8000

# Jalankan menggunakan gunicorn
# Gunicorn akan secara otomatis mendeteksi run.so sebagai module 'run'
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "run:app", "--workers", "2", "--threads", "4", "--timeout", "120"]
