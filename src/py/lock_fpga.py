import os
import argparse
from datetime import datetime, timedelta
import getpass
import json

LOCK_FILE = "/tmp/fpga_lock.json"  # Path to the lock file in JSON format

def read_lock_file():
    """Read and parse the lock file (in JSON format), returning its contents as a dictionary."""
    if os.path.exists(LOCK_FILE):
        with open(LOCK_FILE, 'r') as lock:
            return json.load(lock)  # Parse the JSON data
    return None

def check_lock():
    """Check the status of the lock file."""
    lock_data = read_lock_file()
    if lock_data:
        print(f"Lock file exists. Created by: {lock_data.get('User')}")
        print(f"Creation time: {lock_data.get('Creation Time')}")
        print(f"Estimated end time: {lock_data.get('Estimated End Time')}")
    else:
        print("No lock file exists. FPGA is available.")

def lock_fpga(hours):
    """Lock the FPGA by creating a lock file in JSON format."""
    lock_data = read_lock_file()
    current_user = getpass.getuser()
    
    if lock_data:
        existing_user = lock_data.get('User')
        
        if existing_user == current_user:
            # Lock exists for the current user, ask if they want to overwrite
            confirm = input("A lock already exists for your user. Do you want to overwrite it? (y/n): ")
            if confirm.lower() != 'y':
                print("Lock not overwritten. FPGA remains locked.")
                return
            else:
                print("Overwriting the existing lock.")
        else:
            print(f"The FPGA is currently locked by {existing_user}.")
            print(f"Use the unlock command first to unlock the FPGA.")
            return
    
    creation_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    estimated_end_time = (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    
    # Create or overwrite the lock file with all necessary details in JSON format
    lock_data = {
        "User": current_user,
        "Creation Time": creation_time,
        "Estimated End Time": estimated_end_time
    }
    
    with open(LOCK_FILE, 'w') as lock:
        json.dump(lock_data, lock, indent=4)
    
    # Set the file permissions to 0666 (read and write for owner, group, and others)
    os.chmod(LOCK_FILE, 0o666)
    
    print(f"FPGA locked by {current_user}.")
    print(f"Lock file created. Estimated end time: {estimated_end_time}")

def unlock_fpga():
    """Unlock the FPGA by removing the lock file."""
    lock_data = read_lock_file()
    
    if lock_data:
        existing_user = lock_data.get('User')
        current_user = getpass.getuser()
        
        if existing_user == current_user:
            os.remove(LOCK_FILE)
            print("FPGA unlocked. Lock file removed.")
        else:
            confirm = input(f"The lock file was created by {existing_user}, not you. Are you sure you want to unlock it? (y/n): ")
            if confirm.lower() == 'y':
                os.remove(LOCK_FILE)
                print("FPGA unlocked. Lock file removed.")
            else:
                print("Unlocking aborted. FPGA remains locked.")
    else:
        print("No lock file exists. FPGA is available.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FPGA Lock Management")
    subparsers = parser.add_subparsers(dest='command')

    # Subcommand for checking the lock status
    subparsers.add_parser('check', help="Check the lock status of the FPGA")

    # Subcommand for locking the FPGA
    lock_parser = subparsers.add_parser('lock', help="Lock the FPGA")
    lock_parser.add_argument('hours', type=int, help="The estimated time in hours for how long the FPGA will be locked")

    # Subcommand for unlocking the FPGA
    subparsers.add_parser('unlock', help="Unlock the FPGA")

    args = parser.parse_args()

    if args.command == 'check':
        check_lock()
    elif args.command == 'lock':
        lock_fpga(args.hours)
    elif args.command == 'unlock':
        unlock_fpga()
    else:
        print("Invalid command. Use --help for usage instructions.")
