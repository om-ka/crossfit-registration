import requests
from urllib.parse import quote


async def send_push_notification(key, title, message, body=None):
    """
    Send push notification using Alertzy service
    
    Args:
        key (str): Alertzy account key
        title (str): Notification title
        message (str): Notification message
        body (str, optional): Request body (unused in current implementation)
    """
    try:
        # Send alert push notification to phone
        fetch_url = f"https://alertzy.app/send?accountKey={quote(key)}&title={quote(title)}&message={quote(message)}"
        
        response = requests.post(
            fetch_url,
            data=body,
            headers={
                'Content-Type': 'application/json' if body else 'application/x-www-form-urlencoded'
            }
        )
        
        if response.status_code == 200:
            print(f"Push notification sent successfully: {title}")
        else:
            print(f"Push notification failed with status {response.status_code}")
            
    except Exception as error:
        print(f"Push notification didn't sent | {error}")


def send_push_notification_sync(key, title, message, body=None):
    """
    Synchronous version of send_push_notification for easier use
    """
    try:
        # Send alert push notification to phone
        fetch_url = f"https://alertzy.app/send?accountKey={quote(key)}&title={quote(title)}&message={quote(message)}"
        
        response = requests.post(
            fetch_url,
            data=body,
            headers={
                'Content-Type': 'application/json' if body else 'application/x-www-form-urlencoded'
            }
        )
        
        if response.status_code == 200:
            print(f"Push notification sent successfully: {title}")
        else:
            print(f"Push notification failed with status {response.status_code}")
            
    except Exception as error:
        print(f"Push notification didn't sent | {error}")
