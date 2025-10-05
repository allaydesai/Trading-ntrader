"""Simple TWS connection diagnostic script."""

import socket
import sys

def test_socket_connection(host="127.0.0.1", port=7497):
    """Test basic socket connection to TWS."""
    print(f"Testing socket connection to {host}:{port}...")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, port))

        if result == 0:
            print(f"✅ Port {port} is OPEN and accepting connections")

            # Try to read initial data
            sock.settimeout(2)
            try:
                data = sock.recv(1024)
                if data:
                    print(f"✅ Received data from TWS: {len(data)} bytes")
                else:
                    print("⚠️  Connected but no data received")
            except socket.timeout:
                print("⚠️  Connected but timed out waiting for response")

            sock.close()
            return True
        else:
            print(f"❌ Port {port} is CLOSED or not accepting connections")
            return False

    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False

def main():
    """Run diagnostics."""
    print("=" * 60)
    print("TWS Connection Diagnostics")
    print("=" * 60)

    # Test socket connection
    if test_socket_connection():
        print("\n✅ Basic network connectivity is working")
        print("\nPossible issues:")
        print("  1. API connections not enabled in TWS settings")
        print("  2. Connection prompt in TWS needs to be accepted")
        print("  3. Master API client ID restriction in TWS")
        print("\nNext steps:")
        print("  1. Check TWS: File → Global Configuration → API → Settings")
        print("  2. Ensure 'Enable ActiveX and Socket Clients' is CHECKED")
        print("  3. Look for connection prompt popup in TWS")
        print("  4. Check TWS Activity Monitor for errors")
    else:
        print("\n❌ Cannot connect to TWS")
        print("\nTroubleshooting:")
        print("  1. Verify TWS is running")
        print("  2. Check TWS is using port 7497")
        print("  3. Restart TWS completely")

    print("=" * 60)

if __name__ == "__main__":
    main()
