
        
from locust import HttpUser, task, between

class WebsiteUser(HttpUser):
    wait_time = between(1, 2)

    @task
    def login(self):
        with self.client.post("https://exam-backend-117372286918.asia-south1.run.app/login", json={
            "username": "ADMIN",
            "password": "PS*100"
        }, catch_response=True) as response:

            if response.status_code != 200:
                response.failure("Login failed")        