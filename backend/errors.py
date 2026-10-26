"""Các lỗi đầu vào và lỗi dịch vụ trả về cho API."""

class InputError(ValueError):
    pass


class ProviderError(RuntimeError):
    pass
