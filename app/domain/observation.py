from datetime import timedelta


# Agent clocks may lead the Broker clock by at most this amount. Keep the
# corresponding reservation deduction until even that lead cannot explain
# an observation timestamp newer than the commit.
OBSERVATION_CLOCK_SKEW = timedelta(seconds=30)
