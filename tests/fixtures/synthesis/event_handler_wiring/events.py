"""Emitter/handler pairs sharing an event suffix."""


def emit_order_created(order_id):
    return order_id


def on_order_created(order_id):
    return ("handled", order_id)


def dispatch_payment_failed(payment_id):
    return payment_id


def handle_payment_failed(payment_id):
    return ("recorded", payment_id)
