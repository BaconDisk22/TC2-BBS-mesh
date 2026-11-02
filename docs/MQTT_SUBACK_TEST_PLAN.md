# MQTT subscription acknowledgement tests

Verify that a successful connection does not imply subscription success. A broker SUBACK must be observed before reporting the topic as subscribed. Test rejected subscriptions, disconnects, and retries with a fake client. Keep all outgoing publishing disabled.
