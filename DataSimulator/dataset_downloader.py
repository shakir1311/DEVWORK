"""
Dataset Download Worker
Downloads PhysioNet dataset in background thread with progress updates.
Downloads only REFERENCE.csv initially, individual .mat files downloaded on demand.
Supports multithreaded bulk downloads for speed.
"""

import os
import urllib.request
import csv
import logging
from typing import Optional, Callable, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from PyQt6.QtCore import QThread, pyqtSignal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DatasetDownloadWorker(QThread):
    """Background worker thread for downloading dataset."""
    
    # Signals
    sig_progress = pyqtSignal(str, int)  # (message, percent)
    sig_status = pyqtSignal(str)  # Status message
    sig_finished = pyqtSignal(bool, str)  # (success, message)
    
    # Download modes
    MODE_REFERENCE_ONLY = "reference_only"
    MODE_BULK_DOWNLOAD = "bulk_download"
    
    # Dataset URLs
    DATASET_BASE_URL = "https://archive.physionet.org/pn3/challenge/2017/training/"
    REFERENCE_URL = "https://archive.physionet.org/pn3/challenge/2017/training/REFERENCE.csv"
    
    # Local paths
    DATASET_DIR = "./data/cinc2017"
    TRAINING_DIR = os.path.join(DATASET_DIR, "training")
    REFERENCE_FILE = os.path.join(DATASET_DIR, "REFERENCE.csv")
    
    # Multithreading configuration
    MAX_WORKERS = 100  # Number of concurrent download threads (I/O-bound, so high parallelism is beneficial)
    
    def __init__(self, mode: str = "reference_only"):
        """
        Initialize download worker.
        
        Args:
            mode: "reference_only" or "bulk_download"
        """
        super().__init__()
        self.mode = mode
        self.progress_callback: Optional[Callable] = None
        self._download_lock = Lock()  # Thread-safe counter updates
        self._success_count = 0
        self._failed_count = 0
        self._total_count = 0
    
    def run(self):
        """Main download process - downloads REFERENCE.csv or all patient files."""
        try:
            # Create directories
            self.sig_status.emit("Creating directories...")
            os.makedirs(self.DATASET_DIR, exist_ok=True)
            os.makedirs(self.TRAINING_DIR, exist_ok=True)
            
            # Download reference file
            self.sig_status.emit("Downloading REFERENCE.csv...")
            self.sig_progress.emit("Downloading reference file...", 0)
            
            def ref_progress_callback(block_num, block_size, total_size):
                if total_size > 0:
                    downloaded = block_num * block_size
                    percent = min(100, int((downloaded / total_size) * 100))
                    kb_downloaded = downloaded / 1024
                    kb_total = total_size / 1024
                    self.sig_progress.emit(
                        f"Downloading REFERENCE.csv: {kb_downloaded:.1f} / {kb_total:.1f} KB",
                        percent
                    )
            
            urllib.request.urlretrieve(self.REFERENCE_URL, self.REFERENCE_FILE, ref_progress_callback)
            self.sig_progress.emit("Reference file downloaded!", 100)
            
            # Get patient list (format: A00/A00001)
            patient_ids = []
            with open(self.REFERENCE_FILE, 'r') as f:
                reader = csv.reader(f)
                patient_ids = [row[0].strip() for row in reader if len(row) >= 2]
            
            patient_count = len(patient_ids)
            self.sig_status.emit(f"Found {patient_count} patients")
            
            # If bulk download mode, download all patient files (multithreaded)
            if self.mode == self.MODE_BULK_DOWNLOAD:
                self.sig_status.emit(f"Bulk downloading {patient_count} patient files using {self.MAX_WORKERS} threads...")
                
                # Filter out already downloaded files (both .mat and .hea must exist)
                files_to_download = []
                self._success_count = 0
                self._failed_count = 0
                
                for patient_id in patient_ids:
                    mat_file = os.path.join(self.TRAINING_DIR, f"{patient_id}.mat")
                    hea_file = os.path.join(self.TRAINING_DIR, f"{patient_id}.hea")
                    # Only skip if both files exist
                    if os.path.exists(mat_file) and os.path.exists(hea_file):
                        self._success_count += 1  # Already have both files
                    else:
                        files_to_download.append(patient_id)
                
                self._total_count = len(files_to_download)
                
                if self._total_count == 0:
                    self.sig_progress.emit("All files already downloaded!", 100)
                    self.sig_finished.emit(
                        True,
                        f"All {patient_count} patient files already downloaded!"
                    )
                else:
                    self.sig_status.emit(
                        f"Downloading {self._total_count} files ({self._success_count} already cached)..."
                    )
                    
                    # Use ThreadPoolExecutor for concurrent downloads
                    with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
                        # Submit all download tasks
                        future_to_patient = {
                            executor.submit(self._download_single_file, patient_id): patient_id
                            for patient_id in files_to_download
                        }
                        
                        # Process completed downloads
                        completed = 0
                        for future in as_completed(future_to_patient):
                            patient_id = future_to_patient[future]
                            try:
                                success = future.result()
                                with self._download_lock:
                                    if success:
                                        self._success_count += 1
                                    else:
                                        self._failed_count += 1
                                    
                                    completed += 1
                                    
                                    # Update progress
                                    percent = int((completed / self._total_count) * 100)
                                    self.sig_progress.emit(
                                        f"Downloaded {completed}/{self._total_count} files "
                                        f"({self._failed_count} failed)",
                                        percent
                                    )
                                    
                                    # Status update every 100 files
                                    if completed % 100 == 0:
                                        self.sig_status.emit(
                                            f"Progress: {completed}/{self._total_count} completed, "
                                            f"{self._failed_count} failed..."
                                        )
                                        
                            except Exception as e:
                                logger.error(f"Error processing download for {patient_id}: {str(e)}")
                                with self._download_lock:
                                    self._failed_count += 1
                                    completed += 1
                    
                    # Final status
                    total_success = self._success_count
                    total_failed = self._failed_count
                    
                    self.sig_progress.emit("Bulk download complete!", 100)
                    self.sig_status.emit(f"Downloaded {total_success}/{patient_count} files, {total_failed} failed")
                    
                    if total_failed == 0:
                        self.sig_finished.emit(
                            True,
                            f"Successfully downloaded all {patient_count} patient files!"
                        )
                    elif total_success > 0:
                        self.sig_finished.emit(
                            True,
                            f"Downloaded {total_success} of {patient_count} files.\n"
                            f"{total_failed} files failed (will retry on demand)."
                        )
                    else:
                        self.sig_finished.emit(False, "Bulk download failed for all files")
            else:
                # Reference only mode
                self.sig_status.emit(f"Reference file ready! ({patient_count} patients available)")
                self.sig_finished.emit(
                    True, 
                    f"Successfully downloaded REFERENCE.csv with {patient_count} patients!\n\n"
                    "Individual ECG files will be downloaded on demand when you select a patient."
                )
            
        except Exception as e:
            error_msg = f"Download failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            self.sig_status.emit(f"Error: {str(e)}")
            self.sig_finished.emit(False, error_msg)
    
    def _download_single_file(self, patient_id: str) -> bool:
        """
        Download a single patient file (both .mat and .hea files).
        
        Args:
            patient_id: Patient ID with path (e.g., 'A00/A00001')
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Create subdirectory if needed
            subdir = os.path.dirname(patient_id)
            patient_subdir = os.path.join(self.TRAINING_DIR, subdir)
            os.makedirs(patient_subdir, exist_ok=True)
            
            # Download both .mat and .hea files
            mat_file = os.path.join(self.TRAINING_DIR, f"{patient_id}.mat")
            hea_file = os.path.join(self.TRAINING_DIR, f"{patient_id}.hea")
            
            mat_url = f"{self.DATASET_BASE_URL}{patient_id}.mat"
            hea_url = f"{self.DATASET_BASE_URL}{patient_id}.hea"
            
            # Download .mat file
            urllib.request.urlretrieve(mat_url, mat_file)
            
            # Download .hea file
            urllib.request.urlretrieve(hea_url, hea_file)
            
            # Verify both files
            mat_ok = os.path.exists(mat_file) and os.path.getsize(mat_file) > 0
            hea_ok = os.path.exists(hea_file) and os.path.getsize(hea_file) > 0
            
            if mat_ok and hea_ok:
                logger.debug(f"Successfully downloaded {patient_id}.mat and {patient_id}.hea")
                return True
            else:
                logger.error(f"Download incomplete: mat={mat_ok}, hea={hea_ok}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to download {patient_id} files: {str(e)}")
            return False
    
    @staticmethod
    def is_dataset_downloaded() -> bool:
        """Check if REFERENCE.csv is downloaded (individual files downloaded on demand)."""
        ref_file = DatasetDownloadWorker.REFERENCE_FILE
        return os.path.exists(ref_file)
    
    @staticmethod
    def download_patient_file(patient_id: str, progress_callback: Optional[Callable] = None) -> bool:
        """
        Download individual patient files (.mat and .hea) on demand.
        
        Args:
            patient_id: Patient ID with path (e.g., 'A00/A00001')
            progress_callback: Optional callback(block_num, block_size, total_size)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Ensure training directory exists
            os.makedirs(DatasetDownloadWorker.TRAINING_DIR, exist_ok=True)
            
            # Create subdirectory for patient (e.g., A00/)
            subdir = os.path.dirname(patient_id)
            patient_subdir = os.path.join(DatasetDownloadWorker.TRAINING_DIR, subdir)
            os.makedirs(patient_subdir, exist_ok=True)
            
            # Construct file paths
            mat_file = os.path.join(DatasetDownloadWorker.TRAINING_DIR, f"{patient_id}.mat")
            hea_file = os.path.join(DatasetDownloadWorker.TRAINING_DIR, f"{patient_id}.hea")
            
            # Skip if both files already downloaded
            if os.path.exists(mat_file) and os.path.exists(hea_file):
                logger.info(f"Patient files {patient_id}.mat and {patient_id}.hea already exist")
                return True
            
            # Download .mat file
            mat_url = f"{DatasetDownloadWorker.DATASET_BASE_URL}{patient_id}.mat"
            logger.info(f"Downloading {patient_id}.mat from {mat_url}")
            urllib.request.urlretrieve(mat_url, mat_file, progress_callback)
            
            # Download .hea file
            hea_url = f"{DatasetDownloadWorker.DATASET_BASE_URL}{patient_id}.hea"
            logger.info(f"Downloading {patient_id}.hea from {hea_url}")
            urllib.request.urlretrieve(hea_url, hea_file)
            
            # Verify both downloads
            mat_ok = os.path.exists(mat_file) and os.path.getsize(mat_file) > 0
            hea_ok = os.path.exists(hea_file) and os.path.getsize(hea_file) > 0
            
            if mat_ok and hea_ok:
                logger.info(f"Successfully downloaded {patient_id}.mat and {patient_id}.hea")
                return True
            else:
                logger.error(f"Download incomplete: mat={mat_ok}, hea={hea_ok}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to download {patient_id} files: {str(e)}")
            return False

